"""Scheduled notifications — day before, day of, prime-time reminder, weekly digest."""

from datetime import date, datetime, timedelta
import logging

from telegram.ext import ContextTypes

import pytz

from bot.config import PRIME_TIME_HOUR, TIMEZONE, USER_ID
from bot.models import Post, Streak, get_session
from bot.news import run_digest

logger = logging.getLogger(__name__)

TZ = pytz.timezone(TIMEZONE)


def _today() -> date:
    return datetime.now(TZ).date()


# ── Day-before notification (runs every day at 21:00) ────────────────────────

async def notify_day_before(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a reminder about tomorrow's posts."""
    tomorrow = _today() + timedelta(days=1)

    with get_session() as session:
        posts = (
            session.query(Post)
            .filter(Post.date == tomorrow, Post.notified_day_before == False)
            .all()
        )
        if not posts:
            return

        lines = ["📢 *Завтра по плану:*\n"]
        for p in posts:
            lines.append(f"• {p.format} — {p.topic}")
            p.notified_day_before = True

        session.commit()

    await ctx.bot.send_message(chat_id=USER_ID, text="\n".join(lines), parse_mode="Markdown")


# ── Day-of notification (runs every day at 09:00) ───────────────────────────

async def notify_day_of(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a reminder about today's posts."""
    today = _today()

    with get_session() as session:
        posts = (
            session.query(Post)
            .filter(Post.date == today, Post.notified_day_of == False)
            .all()
        )
        if not posts:
            return

        lines = ["🎯 *Сегодня нужно выложить:*\n"]
        for p in posts:
            fmt_lower = p.format.lower()
            if fmt_lower in ("выходной",):
                lines.append(f"• {p.format} — {p.topic} (выходной, отдыхай 🏖)")
            else:
                lines.append(f"• {p.format} — {p.topic}")
            p.notified_day_of = True

        session.commit()

    await ctx.bot.send_message(chat_id=USER_ID, text="\n".join(lines), parse_mode="Markdown")


# ── Prime-time reminder (runs at PRIME_TIME_HOUR - 2) ───────────────────────

async def notify_prime_time(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Remind 2 hours before prime-time about unpublished posts."""
    today = _today()

    with get_session() as session:
        posts = (
            session.query(Post)
            .filter(
                Post.date == today,
                Post.status != "published",
                Post.notified_prime_time == False,
                Post.format.notin_(["выходной", "Выходной"]),
            )
            .all()
        )
        if not posts:
            return

        lines = [f"⏰ *Через 2 часа прайм-тайм ({PRIME_TIME_HOUR}:00)!*\n\nЕщё не опубликовано:\n"]
        for p in posts:
            lines.append(f"• {p.format} — {p.topic}")
            p.notified_prime_time = True

        session.commit()

    await ctx.bot.send_message(chat_id=USER_ID, text="\n".join(lines), parse_mode="Markdown")


# ── Daily news digest (runs every day at 09:00) ─────────────────────────────

async def news_digest(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Fetch RSS news, analyse with Claude, send digest."""
    try:
        digest = run_digest()
        if not digest:
            await ctx.bot.send_message(
                chat_id=USER_ID,
                text="📭 Новостей за последние 24 часа нет — проверю завтра!",
            )
            return
        # Split if > 4000 chars
        limit = 4000
        text = digest
        while text:
            chunk, text = text[:limit], text[limit:]
            await ctx.bot.send_message(
                chat_id=USER_ID,
                text=chunk,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
        await ctx.bot.send_message(
            chat_id=USER_ID,
            text="👆 Сохрани идею командой `/save_news N`",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error("news_digest job failed: %s", e)
        await ctx.bot.send_message(
            chat_id=USER_ID,
            text=f"❌ Ошибка новостного дайджеста: {e}",
        )


# ── Weekly digest (runs every Sunday at 10:00) ──────────────────────────────

async def weekly_digest(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the plan for the upcoming week."""
    today = _today()
    # Next Monday
    next_monday = today + timedelta(days=(7 - today.weekday()))
    next_sunday = next_monday + timedelta(days=6)

    with get_session() as session:
        posts = (
            session.query(Post)
            .filter(Post.date >= next_monday, Post.date <= next_sunday)
            .order_by(Post.date)
            .all()
        )
        streak = session.query(Streak).first()

    if not posts:
        text = (
            f"📅 *План на следующую неделю* "
            f"({next_monday.strftime('%d.%m')} – {next_sunday.strftime('%d.%m')})\n\n"
            f"План пока пуст. Не забудь заполнить контент-план!"
        )
    else:
        lines = [
            f"📅 *План на следующую неделю* "
            f"({next_monday.strftime('%d.%m')} – {next_sunday.strftime('%d.%m')})\n"
        ]
        for p in posts:
            lines.append(f"• {p.date.strftime('%d.%m')} ({p.day_of_week}): {p.format} — {p.topic}")
        lines.append(f"\n🔥 Текущая серия: {streak.current_streak} дней подряд")
        text = "\n".join(lines)

    await ctx.bot.send_message(chat_id=USER_ID, text=text, parse_mode="Markdown")
