"""BlogBot — Telegram bot for managing @belevtsow content plan."""

import logging
from datetime import time

from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

import pytz

from bot.config import BOT_TOKEN, PRIME_TIME_HOUR, TIMEZONE
from bot.handlers import (
    cmd_done,
    cmd_help,
    cmd_ideas,
    cmd_import,
    cmd_plan,
    cmd_start,
    cmd_stats,
    cmd_streak,
    cmd_sync,
    cmd_week,
    handle_text,
)
from bot.models import init_db
from bot.scheduler import notify_day_before, notify_day_of, notify_prime_time, weekly_digest

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TZ = pytz.timezone(TIMEZONE)


def main() -> None:
    logger.info("Initializing database...")
    init_db()

    logger.info("Building Telegram application...")
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # ── Command handlers ─────────────────────────────────────────────────
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("plan", cmd_plan))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(CommandHandler("streak", cmd_streak))
    app.add_handler(CommandHandler("ideas", cmd_ideas))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("sync", cmd_sync))
    app.add_handler(CommandHandler("import", cmd_import))

    # ── Catch-all text handler (saves ideas / handles "готово") ──────────
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # ── Scheduled jobs ───────────────────────────────────────────────────
    job_queue = app.job_queue

    # Day-before reminder — every day at 21:00 Moscow time
    job_queue.run_daily(
        notify_day_before,
        time=time(hour=21, minute=0, tzinfo=TZ),
        name="day_before",
    )

    # Day-of reminder — every day at 09:00 Moscow time
    job_queue.run_daily(
        notify_day_of,
        time=time(hour=9, minute=0, tzinfo=TZ),
        name="day_of",
    )

    # Prime-time reminder — 2 hours before prime-time
    prime_reminder_hour = PRIME_TIME_HOUR - 2
    job_queue.run_daily(
        notify_prime_time,
        time=time(hour=prime_reminder_hour, minute=0, tzinfo=TZ),
        name="prime_time",
    )

    # Weekly digest — every Sunday at 10:00 Moscow time
    job_queue.run_daily(
        weekly_digest,
        time=time(hour=10, minute=0, tzinfo=TZ),
        days=(6,),  # 6 = Sunday
        name="weekly_digest",
    )

    logger.info("Bot started! Listening for messages...")
    app.run_polling()


if __name__ == "__main__":
    main()
