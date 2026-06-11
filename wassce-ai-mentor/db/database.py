from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from dotenv import load_dotenv
import os

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./wassce_mentor.db")

# SQLite needs check_same_thread=False for FastAPI's threaded request handling.
# PostgreSQL uses pool settings tuned for Neon's idle-pause behaviour.
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False}
    )
else:
    engine = create_engine(
        DATABASE_URL,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,   # validate connections before use (handles Neon idle-pause)
        pool_recycle=300,     # recycle every 5 min (Neon pooler is fine with this)
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency: yields a database session and closes it after use."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables if they do not exist."""
    from db import models  # noqa: F401 — register models with Base
    Base.metadata.create_all(bind=engine)
