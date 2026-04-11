"""Telegram bot command handlers."""

from datetime import date, datetime, timedelta
import logging

from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.config import GOOGLE_SHEET_ID, TIMEZONE, USER_ID
from bot.models import Post, Streak, get_session
from bot.sheets import (
    add_idea_to_sheet,
    get_ideas_from_sheet,
    import_from_text,
    sync_from_google_sheets,
    update_post_in_sheet,
    update_tg_post_in_sheet,
)
from bot.pptx_generator import generate_pptx
from bot.news import run_digest, save_news_idea, get_saved_ideas, RSS_SOURCES

import pytz

logger = logging.getLogger(__name__)

TZ = pytz.timezone(TIMEZONE)

# ── Button labels ─────────────────────────────────────────────────────────────

BTN_TODAY  = "📅 Сегодня"
BTN_WEEK   = "📆 Неделя"
BTN_DONE   = "✅ Выложил"
BTN_UNDO   = "↩️ Отменить"
BTN_STATS  = "📊 Статистика"
BTN_IDEAS  = "💡 Идеи"
BTN_STREAK = "🔥 Серия"
BTN_SYNC   = "🔄 Синхронизировать"
BTN_PPTX = "🎨 Презентация"
BTN_NEWS = "🗞 Новости"
BTN_TG   = "✈️ Telegram"

BUTTON_TEXTS = {
    BTN_TODAY, BTN_WEEK, BTN_DONE, BTN_UNDO,
    BTN_STATS, BTN_IDEAS, BTN_STREAK, BTN_SYNC,
    BTN_PPTX, BTN_NEWS, BTN_TG,
}

MONTHS_RU = {
    1: "янв", 2: "фев", 3: "мар", 4: "апр",
    5: "май", 6: "июн", 7: "июл", 8: "авг",
    9: "сен", 10: "окт", 11: "ноя", 12: "дек",
}

DAYS_RU = {
    0: "Пн", 1: "Вт", 2: "Ср", 3: "Чт", 4: "Пт", 5: "Сб", 6: "Вс",
}


def _today() -> date:
    return datetime.now(TZ).date()


def _is_authorized(update: Update) -> bool:
    return update.effective_user and update.effective_user.id == USER_ID


def _fmt_date(d: date) -> str:
    return f"{d.day} {MONTHS_RU[d.month]}"


def _progress_bar(done: int, total: int, length: int = 10) -> str:
    if total == 0:
        return "○" * length
    filled = round(done / total * length)
    return "●" * filled + "○" * (length - filled)


def _esc(text: str) -> str:
    """Экранирование спецсимволов Markdown v1."""
    for ch in ("*", "_", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text


# ── Persistent reply keyboard ─────────────────────────────────────────────────

def _keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [BTN_TODAY,  BTN_WEEK],
            [BTN_DONE,   BTN_UNDO],
            [BTN_TG,     BTN_STATS],
            [BTN_STREAK, BTN_IDEAS],
            [BTN_NEWS,   BTN_SYNC],
            [BTN_PPTX],
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
        rows = [
            (p.date, p.day_of_week, p.format, p.topic, p.status, p.tg_format, p.tg_topic)
            for p in posts
        ]

    if not rows:
        return (
            "На сегодня и завтра постов нет.\n\n"
            "Нажми 🔄 Синхронизировать, чтобы загрузить план."
        )

    sections: list[str] = []
    current_date = None

    for p_date, p_dow, p_fmt, p_topic, p_status, p_tg_fmt, p_tg_topic in rows:
        if p_date != current_date:
            if sections:
                sections.append("")
            if p_date == today:
                sections.append(f"📅 *СЕГОДНЯ · {_fmt_date(today)}*")
            else:
                sections.append(f"📆 *ЗАВТРА · {_fmt_date(tomorrow)}*")
            sections.append("─────────────────")
            current_date = p_date

        icon = "✅" if p_status == "published" else "⏳"
        sections.append(f"📸 {icon} {_esc(p_fmt)}")
        sections.append(f"  ↳ {_esc(p_topic)}")

        if p_tg_topic:
            sections.append(f"✈️ {icon} {_esc(p_tg_fmt or 'Пост')}")
            sections.append(f"  ↳ {_esc(p_tg_topic)}")

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
        rows = [
            (p.date, p.day_of_week, p.format, p.topic, p.status, p.tg_format, p.tg_topic)
            for p in posts
        ]

    if not rows:
        return (
            "На эту неделю контент-план пуст.\n\n"
            "Нажми 🔄 Синхронизировать, чтобы загрузить план."
        )

    published_count = sum(1 for _, __, ___, ____, s, _____, ______ in rows if s == "published")
    total = len(rows)
    bar = _progress_bar(published_count, total)

    lines = [
        f"📆 *{_fmt_date(monday)} — {_fmt_date(sunday)}*",
        f"{bar}  {published_count}/{total}",
        "",
    ]
    for p_date, p_dow, p_fmt, p_topic, p_status, p_tg_fmt, p_tg_topic in rows:
        if p_status == "published":
            icon = "✅"
        elif p_date == today:
            icon = "📌"
        elif p_date < today:
            icon = "❌"
        else:
            icon = "○"
        today_mark = "  ← сегодня" if p_date == today else ""
        lines.append(f"{icon} *{p_dow} {p_date.strftime('%d.%m')}* — 📸 {_esc(p_topic)}{today_mark}")
        if p_tg_topic:
            lines.append(f"   ✈️ {_esc(p_tg_topic)}")

    return "\n".join(lines)


def _build_stats() -> str:
    today = _today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)

    # Начало месяца
    month_start = today.replace(day=1)

    with get_session() as session:
        # Неделя
        week_posts = session.query(Post).filter(
            Post.date >= monday, Post.date <= sunday,
            Post.format.notin_(["выходной", "Выходной"]),
        ).all()
        week_total = len(week_posts)
        week_done = sum(1 for p in week_posts if p.status == "published")
        week_missed = sum(1 for p in week_posts if p.status != "published" and p.date < today)

        # Месяц
        month_posts = session.query(Post).filter(
            Post.date >= month_start, Post.date <= today,
            Post.format.notin_(["выходной", "Выходной"]),
        ).all()
        month_total = len(month_posts)
        month_done = sum(1 for p in month_posts if p.status == "published")

        streak = session.query(Streak).first()
        current_streak = streak.current_streak if streak else 0
        best_streak = streak.max_streak if streak else 0

    week_pct = int(week_done / week_total * 100) if week_total > 0 else 0
    month_pct = int(month_done / month_total * 100) if month_total > 0 else 0
    week_bar = _progress_bar(week_done, week_total)
    month_bar = _progress_bar(month_done, month_total)

    lines = [
        "📊 *Статистика*",
        "",
        f"*Неделя · {_fmt_date(monday)} — {_fmt_date(sunday)}*",
        f"{week_bar}",
        f"Опубликовано: *{week_done}* из *{week_total}*  ({week_pct}%)",
    ]
    if week_missed > 0:
        lines.append(f"Пропущено:      *{week_missed}*")

    lines += [
        "",
        f"*Апрель · {_fmt_date(month_start)} — {_fmt_date(today)}*",
        f"{month_bar}",
        f"Опубликовано: *{month_done}* из *{month_total}*  ({month_pct}%)",
        "",
        f"🔥 Серия сейчас:  *{current_streak}* дн.  |  Рекорд: *{best_streak}* дн.",
    ]

    return "\n".join(lines)


def _build_streak() -> str:
    with get_session() as session:
        streak = session.query(Streak).first()
        current = streak.current_streak if streak else 0
        best = streak.max_streak if streak else 0
        last = streak.last_publish_date if streak else None

    bar = _progress_bar(current, max(best, 1))

    # Ближайший milestone
    milestones = [3, 7, 14, 21, 30, 60, 90, 100]
    next_milestone = next((m for m in milestones if m > current), None)

    lines = [
        "🔥 *Серия публикаций*",
        "",
        f"Сейчас:   *{current}* {'день' if current == 1 else 'дней'} подряд",
        f"Рекорд:   *{best}* дней",
        "",
        f"{bar}  {current}/{best}",
    ]

    if last:
        lines.append(f"\nПоследний пост: *{_fmt_date(last)}*")

    if next_milestone:
        left = next_milestone - current
        lines.append(f"До {next_milestone} дней:  ещё *{left}* {'день' if left == 1 else 'дней'} 💪")

    if current == 0:
        lines.append("\nНачни сегодня — нажми ✅ Выложил!")
    elif current >= best and best > 0:
        lines.append("\n🏆 Это твой личный рекорд!")

    return "\n".join(lines)


def _build_ideas() -> str:
    try:
        ideas = get_ideas_from_sheet()
    except Exception as e:
        logger.warning("Failed to load ideas from sheet: %s", e)
        return "❌ Не удалось загрузить идеи из таблицы."

    if not ideas:
        return (
            "💡 *Идей пока нет*\n\n"
            "Напиши мне любую мысль — сохраню в таблицу."
        )

    lines = [f"💡 *Идеи для роликов* — {len(ideas)} шт.\n"]
    for i, text in enumerate(ideas, 1):
        lines.append(f"*{i}.* {_esc(text)}")

    lines.append("\n_Напиши новую идею — добавлю в таблицу_")
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
            streak.max_streak = max(streak.max_streak, 1)

        streak.last_publish_date = today
        session.commit()
        current_streak = streak.current_streak

    for topic in topics_list:
        try:
            update_post_in_sheet(today, topic, True)
        except Exception as e:
            logger.warning("Sheet update failed: %s", e)

    topics = "\n".join(f"  · {_esc(t)}" for t in topics_list)
    streak_msg = f"🔥 Серия: *{current_streak}* дней подряд!"
    if current_streak == 1:
        streak_msg = "🔥 Серия пошла! День 1"
    return f"✅ *Выложено сегодня:*\n{topics}\n\n{streak_msg}"


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

    for topic in topics_list:
        try:
            update_post_in_sheet(today, topic, False)
        except Exception as e:
            logger.warning("Sheet update failed: %s", e)

    topics = "\n".join(f"  · {_esc(t)}" for t in topics_list)
    return f"↩️ *Отменено:*\n{topics}\n\nПосты снова в плане."


# ── Telegram channel ──────────────────────────────────────────────────────────

def _build_tg_plan() -> str:
    today = _today()
    tomorrow = today + timedelta(days=1)

    with get_session() as session:
        posts = (
            session.query(Post)
            .filter(
                Post.date.in_([today, tomorrow]),
                Post.tg_topic.isnot(None),
                Post.tg_topic != "",
            )
            .order_by(Post.date)
            .all()
        )
        rows = [(p.date, p.tg_format, p.tg_topic, p.status) for p in posts]

    if not rows:
        return (
            "На сегодня и завтра постов для ТГ нет.\n\n"
            "Заполни столбцы «Формат ТГ» и «Тема ТГ» в таблице,\n"
            "затем нажми 🔄 Синхронизировать."
        )

    sections: list[str] = []
    current_date = None
    for p_date, p_fmt, p_topic, p_status in rows:
        if p_date != current_date:
            if sections:
                sections.append("")
            label = "СЕГОДНЯ" if p_date == today else "ЗАВТРА"
            sections.append(f"✈️ *{label} · ТГ · {_fmt_date(p_date)}*")
            sections.append("─────────────────")
            current_date = p_date
        icon = "✅" if p_status == "published" else "⏳"
        sections.append(f"{icon} {_esc(p_fmt or 'Пост')}")
        sections.append(f"  ↳ {_esc(p_topic)}")

    sections.append("")
    sections.append("_Отметить выложенным — кнопка ✅ Выложил_")
    return "\n".join(sections)


# ── NEWS ──────────────────────────────────────────────────────────────────────

async def cmd_news(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    await update.message.reply_text("⏳ Собираю новости и анализирую через AI...")
    try:
        digest = run_digest()
        if digest:
            # Telegram limit 4096 chars — split if needed
            for chunk in _split_message(digest):
                await update.message.reply_text(chunk, parse_mode="Markdown",
                                                disable_web_page_preview=True)
        else:
            await update.message.reply_text(
                "📭 Новостей пока нет или все уже были отправлены.\n\nПроверю позже!",
                reply_markup=_keyboard(),
            )
    except Exception as e:
        logger.exception("News digest failed")
        await update.message.reply_text(
            f"❌ Ошибка при получении новостей:\n{e}\n\n"
            "Убедись что `ANTHROPIC_API_KEY` задан в переменных окружения.",
            reply_markup=_keyboard(),
        )


async def cmd_save_news(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    args = update.message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await update.message.reply_text(
            "Использование: `/save_news 2` — сохранить новость №2 из последнего дайджеста.",
            parse_mode="Markdown",
        )
        return
    n = int(args[1])
    item = save_news_idea(n)
    if item:
        await update.message.reply_text(
            f"✅ *Идея сохранена:*\n\n"
            f"*{item['title']}*\n"
            f"💡 {item['idea']}",
            parse_mode="Markdown",
            reply_markup=_keyboard(),
        )
    else:
        await update.message.reply_text(
            f"❌ Новость №{n} не найдена. Сначала получи дайджест через 🗞 Новости.",
            reply_markup=_keyboard(),
        )


async def cmd_saved_ideas(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    ideas = get_saved_ideas()
    if not ideas:
        await update.message.reply_text(
            "Сохранённых идей из новостей нет.\n\nПолучи дайджест 🗞 и сохраняй через /save\\_news N",
            parse_mode="Markdown",
            reply_markup=_keyboard(),
        )
        return
    lines = ["💾 *Сохранённые идеи из новостей*\n"]
    for i, (title, idea, virality, saved_at) in enumerate(ideas, 1):
        lines.append(f"*{i}.* {_esc(title)}")
        lines.append(f"   💡 {_esc(idea)}")
        lines.append(f"   📊 {virality}/10  ·  {_fmt_date(saved_at.date())}\n")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=_keyboard())


async def cmd_sources(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    lines = ["📡 *Источники новостей*\n"]
    for name, url in RSS_SOURCES:
        lines.append(f"• *{name}*\n  `{url}`")
    lines.append("\n_Хочешь добавить свой источник? Напиши /add\\_source URL_")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=_keyboard())


def _split_message(text: str, limit: int = 4000) -> list[str]:
    """Split long message into chunks ≤ limit chars."""
    if len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > limit:
            chunks.append(current)
            current = ""
        current += line
    if current:
        chunks.append(current)
    return chunks


# ── PPTX ──────────────────────────────────────────────────────────────────────

# Храним chat_id тех, кто ждёт ввода сценария
_pptx_waiting: set[int] = set()


async def _send_pptx(update: Update, scenario: str) -> None:
    chat_id = update.effective_chat.id
    await update.message.reply_text("⏳ Генерирую презентацию...")
    try:
        pptx_bytes = generate_pptx(scenario)
        await update.message.reply_document(
            document=pptx_bytes,
            filename="presentation.pptx",
            caption="🎨 Готово! Открой в PowerPoint или Google Slides.",
            reply_markup=_keyboard(),
        )
    except Exception as e:
        logger.exception("PPTX generation failed")
        await update.message.reply_text(f"❌ Ошибка генерации: {e}", reply_markup=_keyboard())


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
        "📅 *Сегодня* — план на сегодня и завтра\n"
        "📆 *Неделя* — весь план на неделю\n"
        "✅ *Выложил* — отметить пост опубликованным\n"
        "↩️ *Отменить* — снять отметку о публикации\n"
        "📊 *Статистика* — прогресс за неделю и месяц\n"
        "🔥 *Серия* — streak публикаций\n"
        "💡 *Идеи* — список идей для роликов из таблицы\n"
        "🔄 *Синхронизировать* — обновить из Google Таблицы\n\n"
        "💬 Напиши любую мысль — сохраню как идею в таблицу.",
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
        resp = getattr(e, "response", None)
        detail = f"HTTP {resp.status_code}: {resp.text[:200]}" if resp else f"{type(e).__name__}: {e}"
        await update.message.reply_text(f"❌ Ошибка синхронизации:\n{detail}")


# ── /pptx ─────────────────────────────────────────────────────────────────────

async def cmd_pptx(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    scenario = update.message.text.replace("/pptx", "", 1).strip()
    if scenario:
        await _send_pptx(update, scenario)
    else:
        _pptx_waiting.add(update.effective_chat.id)
        await update.message.reply_text(
            "🎨 *Отправь сценарий презентации*\n\n"
            "Формат:\n"
            "```\n"
            "Заголовок презентации\n"
            "---\n"
            "Слайд 1\n"
            "- Пункт 1\n"
            "- Пункт 2\n"
            "---\n"
            "Слайд 2\n"
            "- Пункт 1\n"
            "```\n\n"
            "_Разделяй слайды через_ `---`",
            parse_mode="Markdown",
        )


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


# ── Callback stub ─────────────────────────────────────────────────────────────

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()


# ── Catch-all text handler ────────────────────────────────────────────────────

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    text = update.message.text.strip()
    if not text:
        return

    # Если ждём сценарий для PPTX
    chat_id = update.effective_chat.id
    if chat_id in _pptx_waiting and text not in BUTTON_TEXTS:
        _pptx_waiting.discard(chat_id)
        await _send_pptx(update, text)
        return

    if text == BTN_TODAY:
        await update.message.reply_text(_build_plan(), parse_mode="Markdown", reply_markup=_keyboard())
    elif text == BTN_WEEK:
        await update.message.reply_text(_build_week(), parse_mode="Markdown", reply_markup=_keyboard())
    elif text == BTN_DONE or text.lower() in ("готово", "done", "выложил", "опубликовано"):
        await update.message.reply_text(_do_done(), parse_mode="Markdown", reply_markup=_keyboard())
    elif text == BTN_UNDO:
        await update.message.reply_text(_undo_done(), parse_mode="Markdown", reply_markup=_keyboard())
    elif text == BTN_STATS:
        await update.message.reply_text(_build_stats(), parse_mode="Markdown", reply_markup=_keyboard())
    elif text == BTN_IDEAS:
        await update.message.reply_text(_build_ideas(), parse_mode="Markdown", reply_markup=_keyboard())
    elif text == BTN_STREAK:
        await update.message.reply_text(_build_streak(), parse_mode="Markdown", reply_markup=_keyboard())
    elif text == BTN_TG:
        await update.message.reply_text(_build_tg_plan(), parse_mode="Markdown", reply_markup=_keyboard())
    elif text == BTN_NEWS:
        await update.message.reply_text("⏳ Собираю новости и анализирую через AI...")
        try:
            digest = run_digest()
            if digest:
                for chunk in _split_message(digest):
                    await update.message.reply_text(chunk, parse_mode="Markdown",
                                                    disable_web_page_preview=True)
                await update.message.reply_text("👆 Сохрани идею: `/save_news N`",
                                                parse_mode="Markdown", reply_markup=_keyboard())
            else:
                await update.message.reply_text(
                    "📭 Новостей пока нет или все уже отправлены.",
                    reply_markup=_keyboard(),
                )
        except Exception as e:
            logger.exception("News digest failed")
            await update.message.reply_text(f"❌ Ошибка: {e}", reply_markup=_keyboard())
    elif text == BTN_PPTX:
        _pptx_waiting.add(update.effective_chat.id)
        await update.message.reply_text(
            "🎨 *Отправь сценарий презентации*\n\n"
            "Формат — каждый слайд через `---`:\n\n"
            "`Заголовок презентации`\n"
            "`---`\n"
            "`Слайд 1`\n"
            "`- Пункт`\n"
            "`---`\n"
            "`Слайд 2`\n"
            "`- Пункт`",
            parse_mode="Markdown",
        )
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
            resp = getattr(e, "response", None)
            detail = f"HTTP {resp.status_code}: {resp.text[:200]}" if resp else f"{type(e).__name__}: {e}"
            await update.message.reply_text(f"❌ Ошибка синхронизации:\n{detail}")
    else:
        # Сохранить как идею в таблицу
        try:
            add_idea_to_sheet(text)
            await update.message.reply_text(
                f"💡 *Идея сохранена в таблицу:*\n{_esc(text)}",
                parse_mode="Markdown",
                reply_markup=_keyboard(),
            )
        except Exception as e:
            logger.warning("Failed to save idea to sheet: %s", e)
            await update.message.reply_text(
                f"❌ Не удалось сохранить идею: {e}",
                reply_markup=_keyboard(),
            )
