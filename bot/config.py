import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Telegram
BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]
USER_ID: int = int(os.environ["TELEGRAM_USER_ID"])

# Google Sheets
GOOGLE_SHEET_ID: str = os.getenv("GOOGLE_SHEET_ID", "")
GOOGLE_SHEET_WORKSHEET: str = os.getenv("GOOGLE_SHEET_WORKSHEET", "Sheet1")
GOOGLE_CREDENTIALS_PATH: str = os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")

# Schedule
PRIME_TIME_HOUR: int = int(os.getenv("PRIME_TIME_HOUR", "20"))
TIMEZONE: str = os.getenv("TIMEZONE", "Europe/Moscow")

# Database
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "blogbot.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"
