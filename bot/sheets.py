"""Google Sheets integration — import content plan."""

from datetime import date, datetime
import json
import logging
import os

import gspread
from google.oauth2.service_account import Credentials

from bot.config import GOOGLE_CREDENTIALS_PATH, GOOGLE_SHEET_ID, GOOGLE_SHEET_WORKSHEET
from bot.models import Post, get_session

logger = logging.getLogger(__name__)

# Read + write access
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

# Expected column order in the sheet:
# A: Дата  |  B: День недели  |  C: Формат  |  D: Тема  |  E: Чекбокс (TRUE/FALSE)
DATE_FORMATS = ["%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y"]


def _parse_date(raw: str) -> datetime | None:
    raw = raw.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _get_client() -> gspread.Client:
    import base64
    credentials_b64 = os.getenv("GOOGLE_CREDENTIALS_B64")
    credentials_json = os.getenv("GOOGLE_CREDENTIALS_JSON")

    if credentials_b64:
        info = json.loads(base64.b64decode(credentials_b64).decode())
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    elif credentials_json:
        info = json.loads(credentials_json)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        creds = Credentials.from_service_account_file(GOOGLE_CREDENTIALS_PATH, scopes=SCOPES)
    return gspread.Client(auth=creds)


def sync_from_google_sheets() -> int:
    """Fetch rows from Google Sheets and upsert into the database.

    - New rows are inserted.
    - Existing rows have their status synced from the sheet checkbox.

    Returns the number of new rows added.
    """
    client = _get_client()
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
            raw_status = row[4].strip().upper() if len(row) > 4 else ""
            status = "published" if raw_status == "TRUE" else "planned"

            parsed_date = _parse_date(raw_date)
            if parsed_date is None:
                logger.warning("Skipping row with unparseable date: %s", raw_date)
                continue

            existing = (
                session.query(Post)
                .filter(Post.date == parsed_date, Post.topic == topic.strip())
                .first()
            )
            if existing:
                # Sync status from sheet (sheet is source of truth for checkbox)
                if existing.status != status:
                    existing.status = status
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


def update_post_in_sheet(post_date: date, topic: str, published: bool) -> None:
    """Update the checkbox in column E for the matching row in Google Sheets."""
    client = _get_client()
    spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
    worksheet = spreadsheet.worksheet(GOOGLE_SHEET_WORKSHEET)

    rows = worksheet.get_all_values()
    for i, row in enumerate(rows[1:], start=2):  # 1-indexed, skip header
        if len(row) < 4:
            continue
        parsed_date = _parse_date(row[0])
        if parsed_date == post_date and row[3].strip() == topic.strip():
            worksheet.update_cell(i, 5, published)
            return

    logger.warning("Row not found in sheet for date=%s topic=%s", post_date, topic)


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
