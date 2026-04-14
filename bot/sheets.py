"""Google Sheets integration — import content plan."""

from datetime import date, datetime
import json
import logging
import os
import time

import gspread
from google.oauth2.service_account import Credentials

from bot.config import GOOGLE_CREDENTIALS_PATH, GOOGLE_SHEET_ID, GOOGLE_SHEET_WORKSHEET
from bot.models import Post, get_session

logger = logging.getLogger(__name__)

# A: Дата | B: День | C: Формат IG | D: Тема IG | E: Статус | F: ТГ (разделитель) | G: Формат ТГ | H: Тема ТГ
DATE_FORMATS = ["%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y"]

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _parse_date(raw: str) -> datetime | None:
    raw = raw.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _get_client() -> gspread.Client:
    """Create gspread client using service account credentials."""
    import base64
    credentials_b64 = os.getenv("GOOGLE_CREDENTIALS_B64")
    credentials_json = os.getenv("GOOGLE_CREDENTIALS_JSON")

    if credentials_b64:
        info = json.loads(base64.b64decode(credentials_b64).decode())
        return gspread.service_account_from_dict(info, scopes=SCOPES)
    elif credentials_json:
        info = json.loads(credentials_json)
        return gspread.service_account_from_dict(info, scopes=SCOPES)
    else:
        return gspread.service_account(filename=GOOGLE_CREDENTIALS_PATH, scopes=SCOPES)


def _open_worksheet():
    """Open worksheet with up to 3 retries on failure."""
    last_exc = None
    for attempt in range(3):
        try:
            client = _get_client()
            logger.info("Opening spreadsheet id=%s", GOOGLE_SHEET_ID)
            spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
            logger.info("Opening worksheet name=%r", GOOGLE_SHEET_WORKSHEET)
            ws = spreadsheet.worksheet(GOOGLE_SHEET_WORKSHEET)
            logger.info("Worksheet opened OK")
            return ws
        except Exception as e:
            last_exc = e
            logger.warning("Sheet open failed (attempt %d/3): %s: %s", attempt + 1, type(e).__name__, e)
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise last_exc


def sync_from_google_sheets() -> int:
    """Fetch rows from Google Sheets and upsert into the database.

    - New rows are inserted.
    - Existing rows have their status synced from the sheet checkbox.
    - Posts removed from the sheet are deleted from the DB.

    Returns the number of new rows added.
    """
    worksheet = _open_worksheet()

    rows = worksheet.get_all_values()
    if not rows:
        return 0

    # Skip header row (only rows 2–33 are the content plan, rows 34+ are ideas)
    data_rows = rows[1:33]

    # Build set of (date, topic) that exist in the sheet
    sheet_keys: set[tuple] = set()
    sheet_dates: set = set()
    parsed_rows = []

    for row in data_rows:
        if len(row) < 4:
            continue
        raw_date, day_of_week, fmt, topic = row[0], row[1], row[2], row[3]
        parsed_date = _parse_date(raw_date)
        if parsed_date is None or not topic.strip():
            continue
        sheet_keys.add((parsed_date, topic.strip()))
        sheet_dates.add(parsed_date)
        parsed_rows.append((row, parsed_date, day_of_week, fmt, topic.strip()))

    added = 0
    with get_session() as session:
        # Delete posts whose date is in the sheet but topic is no longer there
        if sheet_dates:
            stale = (
                session.query(Post)
                .filter(Post.date.in_(sheet_dates))
                .all()
            )
            for p in stale:
                if (p.date, p.topic) not in sheet_keys:
                    logger.info("Removing stale post: date=%s topic=%s", p.date, p.topic)
                    session.delete(p)

        for row, parsed_date, day_of_week, fmt, topic in parsed_rows:
            raw_status = row[4].strip().upper() if len(row) > 4 else ""
            status = "published" if raw_status == "TRUE" else "planned"

            # TG columns (F=5, G=6, H=7)
            raw_tg_status = row[5].strip().upper() if len(row) > 5 else ""
            tg_fmt   = row[6].strip() if len(row) > 6 else ""
            tg_topic = row[7].strip() if len(row) > 7 else ""
            tg_status = "published" if raw_tg_status == "TRUE" else "planned"

            existing = (
                session.query(Post)
                .filter(Post.date == parsed_date, Post.topic == topic)
                .first()
            )
            if existing:
                existing.status = status
                existing.format = fmt.strip()
                existing.day_of_week = day_of_week.strip()
                existing.tg_format = tg_fmt or existing.tg_format
                existing.tg_topic = tg_topic or existing.tg_topic
                if raw_tg_status in ("TRUE", "FALSE"):
                    existing.tg_status = tg_status
                continue

            post = Post(
                date=parsed_date,
                day_of_week=day_of_week.strip(),
                format=fmt.strip(),
                topic=topic,
                status=status,
                tg_format=tg_fmt or None,
                tg_topic=tg_topic or None,
                tg_status=tg_status if tg_fmt or tg_topic else None,
            )
            session.add(post)
            added += 1

        session.commit()

    return added


def update_post_in_sheet(post_date: date, topic: str, published: bool) -> None:
    """Update the checkbox in column E for the matching row in Google Sheets."""
    worksheet = _open_worksheet()

    rows = worksheet.get_all_values()
    for i, row in enumerate(rows[1:], start=2):  # 1-indexed, skip header
        if len(row) < 4:
            continue
        parsed_date = _parse_date(row[0])
        if parsed_date == post_date and row[3].strip() == topic.strip():
            # Checkboxes in Google Sheets expect boolean TRUE/FALSE
            worksheet.update([[True if published else False]], f"E{i}")
            logger.info("Sheet updated: row %d → %s", i, published)
            return

    logger.warning("Row not found in sheet for date=%s topic=%s", post_date, topic)


def update_tg_post_in_sheet(post_date: date, topic: str, published: bool) -> None:
    """Update the TG checkbox in column F for the matching row."""
    worksheet = _open_worksheet()
    rows = worksheet.get_all_values()
    for i, row in enumerate(rows[1:], start=2):
        if len(row) < 4:
            continue
        parsed_date = _parse_date(row[0])
        if parsed_date == post_date and row[3].strip() == topic.strip():
            worksheet.update([[True if published else False]], f"F{i}")
            logger.info("TG sheet updated: row %d → %s", i, published)
            return
    logger.warning("TG row not found in sheet for date=%s topic=%s", post_date, topic)


def get_ideas_from_sheet() -> list:
    """Return ideas from Google Sheet rows 34+ (column D, non-empty)."""
    worksheet = _open_worksheet()
    all_values = worksheet.get_all_values()
    ideas = []
    for row in all_values[33:]:  # 0-indexed → sheet row 34+
        if len(row) >= 4 and row[3].strip():
            ideas.append(row[3].strip())
    return ideas


def add_idea_to_sheet(text: str) -> None:
    """Append a new idea to the ideas section (rows 34+) in Google Sheet column D."""
    worksheet = _open_worksheet()
    all_values = worksheet.get_all_values()

    # Find the last occupied row in column D starting from row 34
    last_idea_row = 33  # 1-indexed row 33 = just before row 34
    for i, row in enumerate(all_values[33:], start=34):
        if len(row) >= 4 and row[3].strip():
            last_idea_row = i

    next_row = last_idea_row + 1
    worksheet.update_cell(next_row, 4, text)
    logger.info("Idea added to sheet row %d: %s", next_row, text)


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
