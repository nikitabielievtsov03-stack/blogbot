from datetime import date, datetime

from sqlalchemy import Boolean, Column, Date, DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from bot.config import DATABASE_URL

Base = declarative_base()


class Post(Base):
    """A single content-plan entry (Instagram + Telegram)."""

    __tablename__ = "posts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False)
    day_of_week = Column(String(20), nullable=False)
    # Instagram
    format = Column(String(50), nullable=False)
    topic = Column(Text, nullable=False)
    status = Column(String(30), default="planned")
    notified_day_before = Column(Boolean, default=False)
    notified_day_of = Column(Boolean, default=False)
    notified_prime_time = Column(Boolean, default=False)
    # Telegram
    tg_format = Column(String(100), nullable=True)
    tg_topic = Column(Text, nullable=True)
    tg_status = Column(String(30), default="planned", nullable=True)
    tg_notified_day_before = Column(Boolean, default=False)
    tg_notified_day_of = Column(Boolean, default=False)


class Idea(Base):
    """Quick notes — future post ideas."""

    __tablename__ = "ideas"

    id = Column(Integer, primary_key=True, autoincrement=True)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class SentNews(Base):
    """News articles already sent to the user (deduplication)."""

    __tablename__ = "sent_news"

    id = Column(Integer, primary_key=True, autoincrement=True)
    url = Column(String, unique=True, nullable=False)
    title = Column(Text)
    sent_at = Column(DateTime, default=datetime.utcnow)


class SavedIdea(Base):
    """News-based content ideas saved by the user."""

    __tablename__ = "saved_ideas"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(Text)
    summary = Column(Text)
    idea = Column(Text)
    source_url = Column(String)
    virality = Column(Integer, default=0)
    saved_at = Column(DateTime, default=datetime.utcnow)


class Streak(Base):
    """Tracks the publishing streak."""

    __tablename__ = "streak"

    id = Column(Integer, primary_key=True, autoincrement=True)
    current_streak = Column(Integer, default=0)
    max_streak = Column(Integer, default=0)
    last_publish_date = Column(Date, nullable=True)


engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)


def init_db() -> None:
    Base.metadata.create_all(engine)
    # Миграция: добавить TG-колонки если их нет (SQLite не поддерживает IF NOT EXISTS)
    _new_columns = [
        ("tg_format",               "VARCHAR(100)"),
        ("tg_topic",                "TEXT"),
        ("tg_status",               "VARCHAR(30) DEFAULT 'planned'"),
        ("tg_notified_day_before",  "BOOLEAN DEFAULT 0"),
        ("tg_notified_day_of",      "BOOLEAN DEFAULT 0"),
    ]
    with engine.connect() as conn:
        for col, col_type in _new_columns:
            try:
                conn.execute(__import__("sqlalchemy").text(
                    f"ALTER TABLE posts ADD COLUMN {col} {col_type}"
                ))
                conn.commit()
            except Exception:
                pass  # Колонка уже существует

    with SessionLocal() as session:
        if session.query(Streak).first() is None:
            session.add(Streak(current_streak=0, max_streak=0))
            session.commit()


def get_session() -> Session:
    return SessionLocal()
