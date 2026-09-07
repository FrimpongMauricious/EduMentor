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
    """Create all tables if they do not exist, then apply incremental migrations."""
    from db import models  # noqa: F401 — register models with Base
    Base.metadata.create_all(bind=engine)
    _migrate_add_session_meta()
    _migrate_add_phone_number()
    _migrate_add_student_name()
    _migrate_add_recommendation_fields()
    _migrate_add_ussd_session_id()


def _migrate_add_session_meta() -> None:
    """Add session_meta column to sessions table if missing (safe to call repeatedly)."""
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    try:
        columns = [col["name"] for col in inspector.get_columns("sessions")]
    except Exception:
        return  # Table doesn't exist yet — create_all() will handle it
    if "session_meta" not in columns:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE sessions ADD COLUMN session_meta TEXT"))
            conn.commit()


def _migrate_add_phone_number() -> None:
    """Add phone_number column to students table if missing (safe to call repeatedly)."""
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    try:
        columns = [col["name"] for col in inspector.get_columns("students")]
    except Exception:
        return
    if "phone_number" not in columns:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE students ADD COLUMN phone_number TEXT"))
            conn.commit()

#migrating
def _migrate_add_student_name() -> None:
    """Add name column to students table if missing (safe to call repeatedly)."""
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    try:
        columns = [col["name"] for col in inspector.get_columns("students")]
    except Exception:
        return
    if "name" not in columns:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE students ADD COLUMN name TEXT"))
            conn.commit()


def _migrate_add_recommendation_fields() -> None:
    """Add AI-recommendation columns to students table if missing (safe to call repeatedly)."""
    from sqlalchemy import inspect, text, DateTime
    inspector = inspect(engine)
    try:
        columns = [col["name"] for col in inspector.get_columns("students")]
    except Exception:
        return
    # Compile against the live engine's dialect so this renders TIMESTAMP
    # WITHOUT TIME ZONE on PostgreSQL (prod/Neon) and DATETIME on SQLite
    # (local dev) — matching the naive-datetime DateTime columns already
    # used throughout db/models.py — instead of hardcoding a SQLite-only
    # type name that PostgreSQL rejects.
    datetime_type = DateTime().compile(dialect=engine.dialect)
    with engine.connect() as conn:
        if "recommendation_text" not in columns:
            conn.execute(text("ALTER TABLE students ADD COLUMN recommendation_text TEXT"))
        if "teacher_recommendation_text" not in columns:
            conn.execute(text("ALTER TABLE students ADD COLUMN teacher_recommendation_text TEXT"))
        if "recommendation_generated_at" not in columns:
            conn.execute(text(
                f"ALTER TABLE students ADD COLUMN recommendation_generated_at {datetime_type}"
            ))
        conn.commit()


def _migrate_add_ussd_session_id() -> None:
    """Add ussd_session_id column to sessions table if missing (safe to call repeatedly)."""
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    try:
        columns = [col["name"] for col in inspector.get_columns("sessions")]
    except Exception:
        return
    if "ussd_session_id" not in columns:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE sessions ADD COLUMN ussd_session_id TEXT"))
            conn.commit()


def get_session():
    """Context-manager-free session for non-request code (e.g. reminder cron)."""
    return SessionLocal()
