from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from bot.config import DATABASE_URL


class Base(DeclarativeBase):
    pass


class Post(Base):
    """A single content-plan entry."""

    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    day_of_week: Mapped[str] = mapped_column(String(20), nullable=False)
    format: Mapped[str] = mapped_column(String(50), nullable=False)  # Reels / карусель / сторис / выходной
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="planned")  # planned / published / skipped
    notified_day_before: Mapped[bool] = mapped_column(Boolean, default=False)
    notified_day_of: Mapped[bool] = mapped_column(Boolean, default=False)
    notified_prime_time: Mapped[bool] = mapped_column(Boolean, default=False)


class Idea(Base):
    """Quick notes — future post ideas."""

    __tablename__ = "ideas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Streak(Base):
    """Tracks the publishing streak."""

    __tablename__ = "streak"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    current_streak: Mapped[int] = mapped_column(Integer, default=0)
    max_streak: Mapped[int] = mapped_column(Integer, default=0)
    last_publish_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)


engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)


def init_db() -> None:
    Base.metadata.create_all(engine)
    # Ensure a single streak row exists.
    with SessionLocal() as session:
        if session.query(Streak).first() is None:
            session.add(Streak(current_streak=0, max_streak=0))
            session.commit()


def get_session() -> Session:
    return SessionLocal()
