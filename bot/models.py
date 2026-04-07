from datetime import date, datetime

from sqlalchemy import Boolean, Column, Date, DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from bot.config import DATABASE_URL

Base = declarative_base()


class Post(Base):
    """A single content-plan entry."""

    __tablename__ = "posts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False)
    day_of_week = Column(String(20), nullable=False)
    format = Column(String(50), nullable=False)
    topic = Column(Text, nullable=False)
    status = Column(String(30), default="planned")
    notified_day_before = Column(Boolean, default=False)
    notified_day_of = Column(Boolean, default=False)
    notified_prime_time = Column(Boolean, default=False)


class Idea(Base):
    """Quick notes — future post ideas."""

    __tablename__ = "ideas"

    id = Column(Integer, primary_key=True, autoincrement=True)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


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
    with SessionLocal() as session:
        if session.query(Streak).first() is None:
            session.add(Streak(current_streak=0, max_streak=0))
            session.commit()


def get_session() -> Session:
    return SessionLocal()
