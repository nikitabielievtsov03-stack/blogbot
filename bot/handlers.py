"""Telegram bot command handlers."""

from datetime import date, datetime, timedelta
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.config import GOOGLE_SHEET_ID, TIMEZONE, USER_ID
from bot.models import Idea, Post, Streak, get_session
from bot.sheets import import_from_text, sync_from_google_sheets, update_post_in_sheet

import pytz

logger = logging.getLogger(__name__)

TZ = pytz.timezone(TIMEZONE)

# ── Button labels ─────────────────────────────────────────────────────────────

BTN_TODAY  = "📅 Сегодня"
BTN_WEEK   = "📆 Неделя"
BTN_DONE   = "✅ Готово"
BTN_UNDO   = "↩️ Отменить"
BTN_STATS  = "📊 Статистика"
BTN_IDEAS  = "💡 Идеи"
BTN_STREAK = "🔥 Серия"
BTN_SYNC   = "🔄 Синхронизировать"

BUTTON_TEXTS = {BTN_TODAY, BTN_WEEK, BTN_DONE, BTN_UNDO, BTN_STATS, BTN_IDEAS, BTN_STREAK, BTN_SYNC}

MONTHS_RU = {
    1: "янв", 2: "фев", 3: "мар", 4: "апр",
    5: "май", 6: "июн", 7: "июл", 8: "авг",
    9: "сен", 10: "окт", 11: "ноя", 12: "дек",
}


def _today() -> date:
    return datetime.now(TZ).date()


def _is_authorized(update: Update) -> bool:
    return update.effective_user and update.effective_user.id == USER_ID


def _fmt_date(d: date) -> str:
    return f"{d.day} {MONTHS_RU[d.month]}"


def _progress_bar(done: int, total: int, length: int = 8) -> str:
    if total == 0:
        return "░" * length
    filled = round(done / total * length)
    return "▓" * filled + "░" * (length - filled)


# ── Persistent reply keyboard ─────────────────────────────────────────────────

def _keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [BTN_TODAY,  BTN_WEEK],
            [BTN_DONE,   BTN_UNDO],
            [BTN_STATS,  BTN_IDEAS],
            [BTN_STREAK, BTN_SYNC],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


# ── Logic builders ────────────────────────────────────────────────────────────

def _build_plan() -> str:
    today = _today()
    tomorrow = today + timedelta(days=1)

    with get_session() as session:
        posts = (
            session.query(Post)
            .filter(Post.date.in_([today, tomorrow]))
            .order_by(Post.date)
            .all()
        )
        rows = [(p.date, p.day_of_week, p.format, p.topic, p.status) for p in posts]

    if not rows:
        return (
            "На сегодня и завтра постов нет.\n\n"
            "Нажми 🔄 Синхронизировать, чтобы загрузить план."
        )

    sections: list[str] = []
    current_date = None

    for p_date, p_dow, p_fmt, p_topic, p_status in rows:
        if p_date != current_date:
            if sections:
                sections.append("━━━━━━━━━━━━━━")
            if p_date == today:
                sections.append(f"📋 *СЕГОДНЯ · {p_dow} {_fmt_date(today)}*")
            else:
                sections.append(f"📆 *ЗАВТРА · {p_dow} {_fmt_date(tomorrow)}*")
            current_date = p_date

        icon = "✅" if p_status == "published" else "⏳"
        sections.append(f"{icon} *{p_fmt}*")
        sections.append(f"↳ {p_topic}")

    return "\n".join(sections)


def _build_week() -> str:
    today = _today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)

    with get_session() as session:
        posts = (
            session.query(Post)
            .filter(Post.date >= monday, Post.date <= sunday)
            .order_by(Post.date)
            .all()
        )
        rows = [(p.date, p.day_of_week, p.format, p.topic, p.status) for p in posts]

    if not rows:
        return (
            "На эту неделю контент-план пуст.\n\n"
            "Нажми 🔄 Синхронизировать, чтобы загрузить план."
        )

    lines = [f"📆 *{_fmt_date(monday)} – {_fmt_date(sunday)}*\n"]
    for p_date, p_dow, p_fmt, p_topic, p_status in rows:
        if p_status == "published":
            icon = "✅"
        elif p_date == today:
            icon = "📌"
        else:
            icon = "⬜"
        mark = "  ← сегодня" if p_date == today else ""
        lines.append(f"{icon} *{p_dow} {p_date.strftime('%d.%m')}*  {p_fmt} — {p_topic}{mark}")

    return "\n".join(lines)


def _build_streak() -> str:
    with get_session() as session:
        streak = session.query(Streak).first()
        current = streak.current_streak
        best = streak.max_streak

    bar = _progress_bar(current, max(best, 1))
    return (
        f"🔥 *Серия публикаций*\n\n"
        f"Сейчас:  *{current}* дн. подряд\n"
        f"Рекорд:  *{best}* дн.\n\n"
        f"{bar}  {current}/{best}"
    )


def _build_stats() -> str:
    today = _today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)

    with get_session() as session:
        total = session.query(Post).filter(
            Post.date >= monday, Post.date <= sunday,
            Post.format.notin_(["выходной", "Выходной"]),
        ).count()
        published = session.query(Post).filter(
            Post.date >= monday, Post.date <= sunday,
            Post.status == "published",
        ).count()
        streak = session.query(Streak).first()
        current_streak = streak.current_streak

    pct = int(published / total * 100) if total > 0 else 0
    bar = _progress_bar(published, total)
    return (
        f"📊 *{_fmt_date(monday)} – {_fmt_date(sunday)}*\n\n"
        f"Запланировано   *{total}*\n"
        f"Опубликовано    *{published}*\n\n"
        f"{bar}  *{pct}%*\n\n"
        f"🔥 Серия: *{current_streak}* дней подряд"
    )


def _build_ideas() -> str:
    with get_session() as session:
        ideas = session.query(Idea).order_by(Idea.created_at.desc()).limit(20).all()
        rows = [(idea.text, idea.created_at) for idea in ideas]

    if not rows:
        return (
            "💡 *Идей пока нет*\n\n"
            "Просто напиши мне любую мысль — сохраню как идею для поста."
        )

    lines = ["💡 *Идеи для постов*\n"]
    for i, (text, created_at) in enumerate(rows, 1):
        dt = _fmt_date(created_at.date())
        lines.append(f"*{i}.* {text}  _{dt}_")

    return "\n".join(lines)


def _do_done() -> str:
    today = _today()
    with get_session() as session:
        posts = session.query(Post).filter(Post.date == today, Post.status != "published").all()
        if not posts:
            return (
                "Все сегодняшние посты уже отмечены ✅\n\n"
                "Чтобы снять отметку — нажми ↩️ Отменить."
            )

        topics_list = [p.topic for p in posts]
        for p in posts:
            p.status = "published"

        streak = session.query(Streak).first()
        if streak.last_publish_date == today:
            pass
        elif streak.last_publish_date == today - timedelta(days=1):
            streak.current_streak += 1
            streak.max_streak = max(streak.max_streak, streak.current_streak)
        else:
            streak.current_streak = 1

        streak.last_publish_date = today
        session.commit()
        current_streak = streak.current_streak

    # Sync to Google Sheets
    for topic in topics_list:
        try:
            update_post_in_sheet(today, topic, True)
        except Exception as e:
            logger.warning("Sheet update failed: %s", e)

    topics = "\n".join(f"· {t}" for t in topics_list)
    return (
        f"✅ *Опубликовано сегодня:*\n{topics}\n\n"
        f"🔥 Серия: *{current_streak}* дней подряд!"
    )


def _undo_done() -> str:
    today = _today()
    with get_session() as session:
        posts = session.query(Post).filter(Post.date == today, Post.status == "published").all()
        if not posts:
            return "Нет отмеченных постов на сегодня — нечего отменять."

        topics_list = [p.topic for p in posts]
        for p in posts:
            p.status = "planned"

        streak = session.query(Streak).first()
        if streak.last_publish_date == today:
            streak.current_streak = max(0, streak.current_streak - 1)
            streak.last_publish_date = today - timedelta(days=1) if streak.current_streak > 0 else None

        session.commit()

    # Sync to Google Sheets
    for topic in topics_list:
        try:
            update_post_in_sheet(today, topic, False)
        except Exception as e:
            logger.warning("Sheet update failed: %s", e)

    topics = "\n".join(f"· {t}" for t in topics_list)
    return f"↩️ *Статус сброшен:*\n{topics}\n\nПосты снова в плане."


# ── /start ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    await update.message.reply_text(
        "Никита, привет 👋\n\n"
        "*@belevtsow · контент-план*\n\n"
        "Панель управления — внизу ↓",
        parse_mode="Markdown",
        reply_markup=_keyboard(),
    )


# ── /help ─────────────────────────────────────────────────────────────────────

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    await update.message.reply_text(
        "📋 *Как пользоваться ботом*\n\n"
        "📅 *Сегодня* — посты на сегодня и завтра\n"
        "📆 *Неделя* — весь план на текущую неделю\n"
        "✅ *Готово* — отметить сегодня как опубликовано\n"
        "📊 *Статистика* — прогресс недели\n"
        "💡 *Идеи* — сохранённые идеи\n"
        "🔥 *Серия* — streak публикаций\n"
        "🔄 *Синхронизировать* — обновить из Google Таблицы\n\n"
        "💬 Любое сообщение сохранится как идея для поста.",
        parse_mode="Markdown",
        reply_markup=_keyboard(),
    )


# ── /plan ─────────────────────────────────────────────────────────────────────

async def cmd_plan(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    await update.message.reply_text(_build_plan(), parse_mode="Markdown", reply_markup=_keyboard())


# ── /week ─────────────────────────────────────────────────────────────────────

async def cmd_week(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    await update.message.reply_text(_build_week(), parse_mode="Markdown", reply_markup=_keyboard())


# ── /done ─────────────────────────────────────────────────────────────────────

async def cmd_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    await update.message.reply_text(_do_done(), parse_mode="Markdown", reply_markup=_keyboard())


# ── /streak ───────────────────────────────────────────────────────────────────

async def cmd_streak(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    await update.message.reply_text(_build_streak(), parse_mode="Markdown", reply_markup=_keyboard())


# ── /ideas ────────────────────────────────────────────────────────────────────

async def cmd_ideas(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    await update.message.reply_text(_build_ideas(), parse_mode="Markdown", reply_markup=_keyboard())


# ── /stats ────────────────────────────────────────────────────────────────────

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    await update.message.reply_text(_build_stats(), parse_mode="Markdown", reply_markup=_keyboard())


# ── /sync ─────────────────────────────────────────────────────────────────────

async def cmd_sync(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    if not GOOGLE_SHEET_ID:
        await update.message.reply_text("Google Sheet ID не настроен. Проверь .env файл.")
        return
    try:
        added = sync_from_google_sheets()
        await update.message.reply_text(
            f"🔄 *Синхронизация завершена*\n\nНовых постов добавлено: *{added}*",
            parse_mode="Markdown",
            reply_markup=_keyboard(),
        )
    except Exception as e:
        logger.exception("Google Sheets sync failed")
        await update.message.reply_text(f"❌ Ошибка синхронизации: {e}")


# ── /import ───────────────────────────────────────────────────────────────────

async def cmd_import(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    text = update.message.text.replace("/import", "", 1).strip()
    if not text:
        await update.message.reply_text(
            "Отправь контент-план в формате (каждая строка):\n"
            "`дата | день | формат | тема`\n\n"
            "Пример:\n"
            "`08.04.26 | ср | Reels | История бренда 12STOREEZ`",
            parse_mode="Markdown",
        )
        return
    added = import_from_text(text)
    await update.message.reply_text(
        f"✅ Импортировано постов: *{added}*",
        parse_mode="Markdown",
        reply_markup=_keyboard(),
    )


# ── Callback stub (for any old inline buttons) ────────────────────────────────

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()


# ── Catch-all text handler ────────────────────────────────────────────────────

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    text = update.message.text.strip()
    if not text:
        return

    if text == BTN_TODAY:
        await update.message.reply_text(_build_plan(), parse_mode="Markdown", reply_markup=_keyboard())
    elif text == BTN_WEEK:
        await update.message.reply_text(_build_week(), parse_mode="Markdown", reply_markup=_keyboard())
    elif text == BTN_DONE or text.lower() in ("готово", "done", "опубликовано"):
        await update.message.reply_text(_do_done(), parse_mode="Markdown", reply_markup=_keyboard())
    elif text == BTN_UNDO:
        await update.message.reply_text(_undo_done(), parse_mode="Markdown", reply_markup=_keyboard())
    elif text == BTN_STATS:
        await update.message.reply_text(_build_stats(), parse_mode="Markdown", reply_markup=_keyboard())
    elif text == BTN_IDEAS:
        await update.message.reply_text(_build_ideas(), parse_mode="Markdown", reply_markup=_keyboard())
    elif text == BTN_STREAK:
        await update.message.reply_text(_build_streak(), parse_mode="Markdown", reply_markup=_keyboard())
    elif text == BTN_SYNC:
        if not GOOGLE_SHEET_ID:
            await update.message.reply_text("Google Sheet ID не настроен.")
            return
        try:
            added = sync_from_google_sheets()
            await update.message.reply_text(
                f"🔄 *Синхронизация завершена*\n\nНовых постов добавлено: *{added}*",
                parse_mode="Markdown",
                reply_markup=_keyboard(),
            )
        except Exception as e:
            logger.exception("Google Sheets sync failed")
            await update.message.reply_text(f"❌ Ошибка синхронизации: {e}")
    else:
        # Save as idea
        with get_session() as session:
            session.add(Idea(text=text))
            session.commit()
        await update.message.reply_text(
            f"💡 *Идея сохранена:*\n{text}",
            parse_mode="Markdown",
            reply_markup=_keyboard(),
        )
