"""News monitoring module — RSS parsing + Claude AI digest."""

from datetime import datetime, timedelta
import logging
import os

import feedparser
import pytz

from bot.config import TIMEZONE
from bot.models import SavedIdea, SentNews, get_session

logger = logging.getLogger(__name__)

TZ = pytz.timezone(TIMEZONE)

RSS_SOURCES = [
    ("Business of Fashion", "https://www.businessoffashion.com/rss/"),
    ("Hypebeast",           "https://hypebeast.com/feed"),
    ("Highsnobiety",        "https://www.highsnobiety.com/feed"),
    ("Forbes Россия",       "https://www.forbes.ru/rss"),
    ("The Blueprint",       "https://theblueprint.ru/rss"),
    ("Shoppers Media",      "https://shoppers.media/rss"),
    ("Vogue Россия",        "https://www.vogue.ru/feed"),
    ("VC.ru",               "https://vc.ru/rss"),
]

CLAUDE_PROMPT = """\
Ты помощник контент-мейкера @belevtsow который ведёт блог про маркетинг брендов одежды и fashion индустрию. \
Проанализируй эти новости и выбери 3-5 самых интересных. Для каждой напиши:
- Заголовок новости
- 2-3 предложения о чём она
- Идея как это можно обыграть в Reels или карусели
- Оценка виральности от 1 до 10
- URL источника (скопируй точно из исходных данных)

Отвечай на русском языке. Фокус на: смены креативных директоров, финансовые результаты брендов, \
неожиданные коллаборации, скандалы, закрытия и запуски брендов, маркетинговые кейсы которые удивляют.

Формат ответа для каждой новости (строго соблюдай метки):
НОВОСТЬ: [заголовок]
ПЕРЕСКАЗ: [2-3 предложения]
ИДЕЯ: [идея для Reels или карусели]
ВИРАЛЬНОСТЬ: [число от 1 до 10]
URL: [ссылка]
---"""

# Хранит последний дайджест в памяти для /save_news N
_last_digest_items: list[dict] = []


# ── RSS fetching ──────────────────────────────────────────────────────────────

def _parse_entry_date(entry) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        raw = getattr(entry, attr, None)
        if raw:
            try:
                return datetime(*raw[:6], tzinfo=pytz.utc).astimezone(TZ)
            except Exception:
                pass
    return None


def fetch_fresh_articles(hours: int = 24) -> list[dict]:
    """Fetch articles published in the last N hours, skip already sent."""
    cutoff = datetime.now(TZ) - timedelta(hours=hours)

    with get_session() as session:
        sent_urls = {row.url for row in session.query(SentNews).all()}

    articles: list[dict] = []

    for source_name, url in RSS_SOURCES:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:15]:
                link = getattr(entry, "link", "") or ""
                if not link or link in sent_urls:
                    continue

                pub_dt = _parse_entry_date(entry)
                if pub_dt and pub_dt < cutoff:
                    continue  # Слишком старая

                summary = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
                # Убрать HTML-теги грубо
                import re
                summary = re.sub(r"<[^>]+>", "", summary)[:600]

                articles.append({
                    "title":   (getattr(entry, "title", "") or "").strip(),
                    "summary": summary.strip(),
                    "link":    link,
                    "source":  source_name,
                    "pub_dt":  pub_dt,
                })

                if len(articles) >= 20:
                    break
        except Exception as e:
            logger.warning("RSS fetch failed for %s: %s", url, e)

        if len(articles) >= 20:
            break

    # Лимит 10 статей в Claude чтобы не превышать токены
    return articles[:10]


# ── Claude analysis ───────────────────────────────────────────────────────────

def analyze_with_claude(articles: list[dict]) -> str:
    """Send articles to Claude API, return raw text response."""
    import anthropic

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY не задан в переменных окружения")

    client = anthropic.Anthropic(api_key=api_key)

    news_block = "\n\n".join(
        f"ИСТОЧНИК: {a['source']}\nЗАГОЛОВОК: {a['title']}\nОПИСАНИЕ: {a['summary']}\nURL: {a['link']}"
        for a in articles
    )

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{
            "role": "user",
            "content": f"{CLAUDE_PROMPT}\n\nВот новости:\n\n{news_block}",
        }],
    )
    return message.content[0].text


# ── Response parsing ──────────────────────────────────────────────────────────

def parse_claude_response(text: str) -> list[dict]:
    """Parse Claude's structured response into list of dicts."""
    items = []
    for block in text.strip().split("---"):
        block = block.strip()
        if not block:
            continue
        item: dict = {}
        for line in block.splitlines():
            line = line.strip()
            if line.startswith("НОВОСТЬ:"):
                item["title"] = line[8:].strip()
            elif line.startswith("ПЕРЕСКАЗ:"):
                item["summary"] = line[9:].strip()
            elif line.startswith("ИДЕЯ:"):
                item["idea"] = line[5:].strip()
            elif line.startswith("ВИРАЛЬНОСТЬ:"):
                raw = line[12:].strip().split("/")[0].strip()
                try:
                    item["virality"] = int(raw)
                except ValueError:
                    item["virality"] = 0
            elif line.startswith("URL:"):
                item["url"] = line[4:].strip()
        if item.get("title") and item.get("summary"):
            items.append(item)
    return items


# ── Telegram formatting ───────────────────────────────────────────────────────

def build_digest_message(items: list[dict]) -> str:
    today = datetime.now(TZ).strftime("%d.%m.%Y")
    lines = [f"🗞 *НОВОСТИ ДНЯ — {today}*\n"]

    for i, item in enumerate(items, 1):
        v = item.get("virality", 0)
        fire = "🔥" * (1 if v < 5 else 2 if v < 8 else 3)
        url = item.get("url", "")

        lines.append("━━━━━━━━━━━━━━━")
        lines.append(f"\n{fire} *{i}. {item['title']}*")
        lines.append(item.get("summary", ""))
        lines.append(f"\n💡 *Идея:* {item.get('idea', '')}")
        lines.append(f"📊 Виральность: *{v}/10*")
        if url:
            lines.append(f"🔗 {url}")
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━")
    lines.append("📌 `/save_news N` — сохранить идею по номеру")

    return "\n".join(lines)


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run_digest() -> str | None:
    """Full pipeline. Returns formatted message or None if nothing to show."""
    global _last_digest_items

    articles = fetch_fresh_articles(hours=24)
    if not articles:
        return None

    try:
        raw = analyze_with_claude(articles)
    except Exception as e:
        logger.error("Claude API error: %s", e)
        raise

    items = parse_claude_response(raw)
    if not items:
        return None

    # Сохранить отправленные URL
    with get_session() as session:
        for a in articles:
            if not session.query(SentNews).filter(SentNews.url == a["link"]).first():
                session.add(SentNews(url=a["link"], title=a["title"]))
        session.commit()

    _last_digest_items = items
    return build_digest_message(items)


# ── Save idea ─────────────────────────────────────────────────────────────────

def save_news_idea(number: int) -> dict | None:
    """Save item N from last digest to DB. Returns saved item or None."""
    if not _last_digest_items or number < 1 or number > len(_last_digest_items):
        return None
    item = _last_digest_items[number - 1]
    with get_session() as session:
        session.add(SavedIdea(
            title=item.get("title", ""),
            summary=item.get("summary", ""),
            idea=item.get("idea", ""),
            source_url=item.get("url", ""),
            virality=item.get("virality", 0),
        ))
        session.commit()
    return item


def get_saved_ideas() -> list[SavedIdea]:
    with get_session() as session:
        ideas = session.query(SavedIdea).order_by(SavedIdea.saved_at.desc()).limit(30).all()
        return [(i.title, i.idea, i.virality, i.saved_at) for i in ideas]
