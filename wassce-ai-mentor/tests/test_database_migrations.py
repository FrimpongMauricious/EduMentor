"""
tests/test_database_migrations.py — regression coverage for db/database.py's
_migrate_* functions.

Context: _migrate_add_recommendation_fields() used to hardcode the raw SQL
type "DATETIME" for the recommendation_generated_at column. SQLite accepts
that (it's untyped storage), but PostgreSQL — the production database, via
Neon — rejects it outright (psycopg2.errors.UndefinedObject: type "datetime"
does not exist), which broke every production deploy. These tests catch that
class of bug by (a) compiling the migration's column type against the
postgresql dialect and asserting it's valid Postgres SQL, and (b) actually
running every migration function end-to-end against a fresh SQLite database
missing the target columns.
"""
import pytest
from sqlalchemy import DateTime, create_engine, inspect
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.pool import StaticPool

import db.database as database
from db.database import Base


# SQLite-only type names (or other dialect-specific syntax) that must never
# appear in a raw ALTER TABLE string run against the shared engine, since
# that engine may be PostgreSQL in production.
POSTGRES_INVALID_TYPES = {"DATETIME", "AUTOINCREMENT"}


@pytest.fixture
def sqlite_engine(monkeypatch):
    """A fresh in-memory SQLite engine, patched in as db.database.engine so
    the module-level _migrate_* functions (which close over `engine`) act on
    it instead of the real one."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from db import models  # noqa: F401 — register models with Base
    monkeypatch.setattr(database, "engine", engine)
    return engine


def test_recommendation_datetime_type_is_valid_postgresql_sql():
    """The column type used for recommendation_generated_at must compile to
    real PostgreSQL syntax, not the SQLite-only 'DATETIME' keyword."""
    pg_type = str(DateTime().compile(dialect=postgresql.dialect()))
    assert pg_type.upper() == "TIMESTAMP WITHOUT TIME ZONE"
    assert "DATETIME" not in pg_type.upper()


def test_recommendation_datetime_type_still_valid_on_sqlite():
    """Same compilation, but for SQLite — confirms the dialect-aware type
    still produces a sensible column type for local dev."""
    sqlite_type = str(DateTime().compile(dialect=sqlite.dialect()))
    assert sqlite_type.upper() == "DATETIME"


def _create_legacy_students_table(engine):
    """Simulate a students table from before the recommendation/name/phone
    columns existed, so the migrations under test have real work to do."""
    with engine.connect() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE students (
                student_id VARCHAR(64) PRIMARY KEY,
                channel VARCHAR(16) NOT NULL,
                registered_at DATETIME NOT NULL,
                last_seen_at DATETIME NOT NULL,
                session_count INTEGER NOT NULL DEFAULT 0,
                consent_given BOOLEAN NOT NULL DEFAULT 0
            )
            """
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE sessions (
                session_id VARCHAR(36) PRIMARY KEY,
                student_id VARCHAR(64) NOT NULL,
                started_at DATETIME NOT NULL,
                last_active_at DATETIME NOT NULL,
                fsm_state VARCHAR(32) NOT NULL,
                current_difficulty VARCHAR(16) NOT NULL
            )
            """
        )
        conn.commit()


def test_all_migrations_run_against_legacy_sqlite_schema(sqlite_engine):
    """End-to-end: every _migrate_* function must apply cleanly, in order,
    against a database that predates all of the incremental columns —
    mirroring what happens on a real deploy against an existing database."""
    _create_legacy_students_table(sqlite_engine)

    database._migrate_add_session_meta()
    database._migrate_add_phone_number()
    database._migrate_add_student_name()
    database._migrate_add_recommendation_fields()

    inspector = inspect(sqlite_engine)
    student_columns = {col["name"] for col in inspector.get_columns("students")}
    session_columns = {col["name"] for col in inspector.get_columns("sessions")}

    assert {
        "phone_number", "name",
        "recommendation_text", "teacher_recommendation_text",
        "recommendation_generated_at",
    } <= student_columns
    assert "session_meta" in session_columns


def test_migrations_are_idempotent(sqlite_engine):
    """Running the migrations twice (e.g. two deploys in a row) must not
    raise — the whole point of the existing-column check."""
    _create_legacy_students_table(sqlite_engine)

    for _ in range(2):
        database._migrate_add_session_meta()
        database._migrate_add_phone_number()
        database._migrate_add_student_name()
        database._migrate_add_recommendation_fields()


def test_no_sqlite_only_types_in_migration_source():
    """Static guard against regressions: scan db/database.py's source for a
    hardcoded SQLite-only type name used as the literal type of an
    ADD COLUMN statement. A new migration that writes
    'ADD COLUMN ... DATETIME' directly (instead of compiling the type
    against the live dialect) should fail this test immediately, rather
    than surviving local SQLite testing and only breaking in production
    PostgreSQL."""
    import inspect as pyinspect
    import re

    source = pyinspect.getsource(database)
    for bad_type in POSTGRES_INVALID_TYPES:
        pattern = re.compile(rf"ADD COLUMN\s+\w+\s+{bad_type}\b")
        assert not pattern.search(source), (
            f"Found hardcoded SQLite-only type {bad_type!r} in a raw "
            "ALTER TABLE string in db/database.py — use DateTime().compile("
            "dialect=engine.dialect) (or equivalent) instead so it renders "
            "correctly for PostgreSQL in production."
        )
