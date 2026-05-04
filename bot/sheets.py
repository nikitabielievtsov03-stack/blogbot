"""Google Sheets integration — import content plan."""

from datetime import date, datetime
import json
import logging
import os
import time

import gspread
from gspread.auth import DEFAULT_SCOPES
from gspread.http_client import BackOffHTTPClient

from bot.config import GOOGLE_CREDENTIALS_PATH, GOOGLE_SHEET_ID, GOOGLE_SHEET_WORKSHEET
from bot.models import Post, get_session

logger = logging.getLogger(__name__)

# A: Дата | B: День | C: Формат IG | D: Тема IG | E: Статус | F: ТГ (разделитель) | G: Формат ТГ | H: Тема ТГ
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
    """Create gspread client using service account credentials with full default scopes."""
    import base64
    credentials_b64 = os.getenv("GOOGLE_CREDENTIALS_B64")
    credentials_json = os.getenv("GOOGLE_CREDENTIALS_JSON")

    if credentials_b64:
        info = json.loads(base64.b64decode(credentials_b64).decode())
    elif credentials_json:
        info = json.loads(credentials_json)
    else:
        info = None

    if info is not None:
        return gspread.service_account_from_dict(
            info, scopes=DEFAULT_SCOPES, http_client=BackOffHTTPClient
        )
    return gspread.service_account(
        filename=GOOGLE_CREDENTIALS_PATH, scopes=DEFAULT_SCOPES, http_client=BackOffHTTPClient
    )


def _open_spreadsheet():
    """Open the spreadsheet with up to 3 retries on failure."""
    last_exc = None
    for attempt in range(3):
        try:
            client = _get_client()
            logger.info("Opening spreadsheet id=%s", GOOGLE_SHEET_ID)
            spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
            logger.info("Spreadsheet opened OK")
            return spreadsheet
        except Exception as e:
            last_exc = e
            logger.warning("Sheet open failed (attempt %d/3): %s: %s", attempt + 1, type(e).__name__, e)
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise last_exc


def _open_worksheet():
    """Open single worksheet (used by idea helpers and sheet update functions)."""
    spreadsheet = _open_spreadsheet()
    ws = spreadsheet.worksheet(GOOGLE_SHEET_WORKSHEET)
    logger.info("Worksheet opened: %r", ws.title)
    return ws


def _collect_rows_from_worksheet(ws) -> list:
    """Return all data rows with a parseable date from a worksheet."""
    all_values = ws.get_all_values()
    result = []
    for row in all_values[1:]:  # skip header
        if len(row) < 4:
            continue
        parsed_date = _parse_date(row[0])
        topic = row[3].strip()
        if parsed_date is None or not topic:
            continue
        result.append((parsed_date, row))
    return result


def sync_from_google_sheets() -> int:
    """Fetch rows from ALL worksheets and fully replace DB entries by date.

    For every date that appears in any sheet: delete all existing DB posts
    for that date, then insert fresh from the sheet. Reads every worksheet
    automatically — adding a new month sheet requires no code changes.

    Returns the number of rows inserted.
    """
    from collections import defaultdict

    spreadsheet = _open_spreadsheet()
    worksheets = spreadsheet.worksheets()
    logger.info("Found %d worksheets: %s", len(worksheets), [ws.title for ws in worksheets])

    date_rows: dict = defaultdict(list)

    for ws in worksheets:
        try:
            for parsed_date, row in _collect_rows_from_worksheet(ws):
                date_rows[parsed_date].append(row)
            logger.info("Collected rows from worksheet %r", ws.title)
        except Exception as e:
            logger.warning("Failed to read worksheet %r: %s", ws.title, e)

    if not date_rows:
        return 0

    inserted = 0
    with get_session() as session:
        for parsed_date, sheet_rows in date_rows.items():
            # Full replace for each date
            session.query(Post).filter(Post.date == parsed_date).delete()

            for row in sheet_rows:
                day_of_week   = row[1].strip()
                fmt           = row[2].strip()
                topic         = row[3].strip()
                raw_status    = row[4].strip().upper() if len(row) > 4 else ""
                status        = "published" if raw_status == "TRUE" else "planned"
                raw_tg_status = row[5].strip().upper() if len(row) > 5 else ""
                tg_fmt        = row[6].strip() if len(row) > 6 else ""
                tg_topic      = row[7].strip() if len(row) > 7 else ""
                tg_status     = "published" if raw_tg_status == "TRUE" else "planned"

                session.add(Post(
                    date=parsed_date,
                    day_of_week=day_of_week,
                    format=fmt,
                    topic=topic,
                    status=status,
                    tg_format=tg_fmt or None,
                    tg_topic=tg_topic or None,
                    tg_status=tg_status if tg_fmt or tg_topic else None,
                ))
                inserted += 1

        session.commit()

    return inserted


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
