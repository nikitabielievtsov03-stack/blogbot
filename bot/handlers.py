"""Telegram bot command handlers."""

from datetime import date, datetime, timedelta
import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.config import GOOGLE_SHEET_ID, TIMEZONE, USER_ID
from bot.models import Idea, Post, Streak, get_session
from bot.sheets import import_from_text, sync_from_google_sheets

import pytz

logger = logging.getLogger(__name__)

TZ = pytz.timezone(TIMEZONE)


def _today() -> date:
    return datetime.now(TZ).date()


def _is_authorized(update: Update) -> bool:
    return update.effective_user and update.effective_user.id == USER_ID


# ── /start ───────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    await update.message.reply_text(
        "Привет, Никита! 👋\n\n"
        "Я — твой бот для управления контент-планом @belevtsow.\n\n"
        "Основные команды:\n"
        "/plan — посты на сегодня и завтра\n"
        "/week — план на текущую неделю\n"
        "/done — отметить сегодняшний пост как опубликованный\n"
        "/streak — текущая серия публикаций\n"
        "/ideas — список сохранённых идей\n"
        "/stats — статистика за неделю\n"
        "/sync — синхронизировать план из Google Sheets\n"
        "/import — загрузить план текстом\n"
        "/help — справка по командам"
    )


# ── /help ────────────────────────────────────────────────────────────────────

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    await update.message.reply_text(
        "📋 *Команды бота*\n\n"
        "/plan — что нужно выложить сегодня и завтра\n"
        "/week — план на текущую неделю\n"
        "/done — отметить сегодняшний пост опубликованным\n"
        "/streak — серия публикаций подряд\n"
        "/ideas — все сохранённые идеи\n"
        "/stats — статистика за последнюю неделю\n"
        "/sync — обновить план из Google Sheets\n"
        "/import — загрузить план текстом (каждая строка:\n"
        "  `дата | день недели | формат | тема`)\n\n"
        "💡 Просто напиши любое сообщение — оно сохранится как идея для будущего поста.",
        parse_mode="Markdown",
    )


# ── /plan — today & tomorrow ────────────────────────────────────────────────

async def cmd_plan(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    today = _today()
    tomorrow = today + timedelta(days=1)

    with get_session() as session:
        posts = (
            session.query(Post)
            .filter(Post.date.in_([today, tomorrow]))
            .order_by(Post.date)
            .all()
        )

    if not posts:
        await update.message.reply_text("На сегодня и завтра постов в плане нет.")
        return

    lines: list[str] = []
    for p in posts:
        day_label = "Сегодня" if p.date == today else "Завтра"
        status_icon = "✅" if p.status == "published" else "⏳"
        lines.append(f"{status_icon} *{day_label}* ({p.day_of_week}): {p.format} — {p.topic}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /week ────────────────────────────────────────────────────────────────────

async def cmd_week(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    today = _today()
    # Monday of the current week.
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)

    with get_session() as session:
        posts = (
            session.query(Post)
            .filter(Post.date >= monday, Post.date <= sunday)
            .order_by(Post.date)
            .all()
        )

    if not posts:
        await update.message.reply_text("На эту неделю контент-план пуст.")
        return

    lines = [f"📅 *План на неделю* ({monday.strftime('%d.%m')} – {sunday.strftime('%d.%m')})\n"]
    for p in posts:
        icon = "✅" if p.status == "published" else ("📌" if p.date == today else "⬜")
        lines.append(f"{icon} {p.date.strftime('%d.%m')} {p.day_of_week}: {p.format} — {p.topic}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /done — mark today's post as published ───────────────────────────────────

async def cmd_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    today = _today()

    with get_session() as session:
        posts = session.query(Post).filter(Post.date == today, Post.status != "published").all()
        if not posts:
            await update.message.reply_text("Все сегодняшние посты уже опубликованы (или их нет в плане).")
            return

        for p in posts:
            p.status = "published"

        # Update streak
        streak = session.query(Streak).first()
        if streak.last_publish_date == today:
            pass  # already counted today
        elif streak.last_publish_date == today - timedelta(days=1):
            streak.current_streak += 1
            streak.max_streak = max(streak.max_streak, streak.current_streak)
        else:
            streak.current_streak = 1

        streak.last_publish_date = today
        session.commit()

        topics = ", ".join(p.topic for p in posts)
        await update.message.reply_text(
            f"✅ Отмечено как опубликовано: {topics}\n"
            f"🔥 Серия: {streak.current_streak} дней подряд!"
        )


# ── /streak ──────────────────────────────────────────────────────────────────

async def cmd_streak(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    with get_session() as session:
        streak = session.query(Streak).first()
        await update.message.reply_text(
            f"🔥 Текущая серия: *{streak.current_streak}* дней подряд\n"
            f"🏆 Рекорд: *{streak.max_streak}* дней",
            parse_mode="Markdown",
        )


# ── /ideas — list saved ideas ───────────────────────────────────────────────

async def cmd_ideas(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    with get_session() as session:
        ideas = session.query(Idea).order_by(Idea.created_at.desc()).limit(30).all()

    if not ideas:
        await update.message.reply_text("Список идей пуст. Просто напиши мне идею — я сохраню.")
        return

    lines = ["💡 *Идеи для постов:*\n"]
    for i, idea in enumerate(ideas, 1):
        dt = idea.created_at.strftime("%d.%m")
        lines.append(f"{i}. {idea.text}  _{dt}_")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /stats — weekly statistics ───────────────────────────────────────────────

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
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

    pct = (published / total * 100) if total > 0 else 0
    await update.message.reply_text(
        f"📊 *Статистика недели* ({monday.strftime('%d.%m')} – {sunday.strftime('%d.%m')})\n\n"
        f"Запланировано: {total}\n"
        f"Опубликовано: {published}\n"
        f"Выполнение: {pct:.0f}%\n"
        f"🔥 Серия: {streak.current_streak} дней",
        parse_mode="Markdown",
    )


# ── /sync — pull from Google Sheets ─────────────────────────────────────────

async def cmd_sync(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    if not GOOGLE_SHEET_ID:
        await update.message.reply_text("Google Sheet ID не настроен. Проверь .env файл.")
        return

    try:
        added = sync_from_google_sheets()
        await update.message.reply_text(f"✅ Синхронизация завершена. Добавлено новых постов: {added}")
    except Exception as e:
        logger.exception("Google Sheets sync failed")
        await update.message.reply_text(f"❌ Ошибка синхронизации: {e}")


# ── /import — import plan from text ──────────────────────────────────────────

async def cmd_import(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    text = update.message.text.replace("/import", "", 1).strip()
    if not text:
        await update.message.reply_text(
            "Отправь контент-план в формате (каждая строка):\n"
            "`дата | день недели | формат | тема`\n\n"
            "Пример:\n"
            "`07.04.2026 | Пн | Reels | История бренда 12STOREEZ`",
            parse_mode="Markdown",
        )
        return

    added = import_from_text(text)
    await update.message.reply_text(f"✅ Импортировано постов: {added}")


# ── Catch-all: save as idea ─────────────────────────────────────────────────

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    text = update.message.text.strip()
    if not text:
        return

    # Check for "готово" / "done" as status shortcut
    if text.lower() in ("готово", "done", "опубликовано"):
        await cmd_done(update, ctx)
        return

    # Otherwise save as idea
    with get_session() as session:
        session.add(Idea(text=text))
        session.commit()

    await update.message.reply_text(f"💡 Идея сохранена: «{text}»")
