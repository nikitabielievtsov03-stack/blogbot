"""Google Sheets integration — import content plan."""

from datetime import datetime
import logging

import gspread
from google.oauth2.service_account import Credentials

from bot.config import GOOGLE_CREDENTIALS_PATH, GOOGLE_SHEET_ID, GOOGLE_SHEET_WORKSHEET
from bot.models import Post, get_session

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]

# Expected column order in the sheet:
# A: Дата  |  B: День недели  |  C: Формат  |  D: Тема  |  E: Статус (optional)
DATE_FORMATS = ["%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"]


def _parse_date(raw: str) -> datetime | None:
    raw = raw.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def sync_from_google_sheets() -> int:
    """Fetch rows from Google Sheets and upsert into the database.

    Returns the number of new rows added.
    """
    creds = Credentials.from_service_account_file(GOOGLE_CREDENTIALS_PATH, scopes=SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
    worksheet = spreadsheet.worksheet(GOOGLE_SHEET_WORKSHEET)

    rows = worksheet.get_all_values()
    if not rows:
        return 0

    # Skip header row
    data_rows = rows[1:]

    added = 0
    with get_session() as session:
        for row in data_rows:
            if len(row) < 4:
                continue
            raw_date, day_of_week, fmt, topic = row[0], row[1], row[2], row[3]
            status = row[4].strip().lower() if len(row) > 4 and row[4].strip() else "planned"

            parsed_date = _parse_date(raw_date)
            if parsed_date is None:
                logger.warning("Skipping row with unparseable date: %s", raw_date)
                continue

            # Check for duplicates (same date + same topic).
            existing = (
                session.query(Post)
                .filter(Post.date == parsed_date, Post.topic == topic.strip())
                .first()
            )
            if existing:
                continue

            post = Post(
                date=parsed_date,
                day_of_week=day_of_week.strip(),
                format=fmt.strip(),
                topic=topic.strip(),
                status=status,
            )
            session.add(post)
            added += 1

        session.commit()

    return added


def import_from_text(text: str) -> int:
    """Import content plan from a plain-text message.

    Each line: date | day_of_week | format | topic
    Separator: | or tab.
    """
    added = 0
    with get_session() as session:
        for line in text.strip().splitlines():
            line = line.strip()
            if not line:
                continue

            # Try pipe separator, then tab
            if "|" in line:
                parts = [p.strip() for p in line.split("|")]
            elif "\t" in line:
                parts = [p.strip() for p in line.split("\t")]
            else:
                continue

            if len(parts) < 4:
                continue

            raw_date, day_of_week, fmt, topic = parts[0], parts[1], parts[2], parts[3]
            parsed_date = _parse_date(raw_date)
            if parsed_date is None:
                continue

            existing = (
                session.query(Post)
                .filter(Post.date == parsed_date, Post.topic == topic)
                .first()
            )
            if existing:
                continue

            post = Post(
                date=parsed_date,
                day_of_week=day_of_week,
                format=fmt,
                topic=topic,
            )
            session.add(post)
            added += 1

        session.commit()

    return added
