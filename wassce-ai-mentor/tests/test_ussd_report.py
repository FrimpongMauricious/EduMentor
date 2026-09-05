"""
tests/test_ussd_report.py — USSD "My Report" menu option (option 5 from
the subject-selection screen). Reuses the dashboard's own aggregation
logic (api/routes/dashboard._student_detail) to render a short on-screen
summary — no SMS, no new external API calls, no new query path.

This file only covers the new, additive USSD surface. Existing USSD
navigation ("1.Next 2.Menu 0.Stop"), question delivery, and grading are
covered by tests/test_fsm.py and tests/test_new_features.py and are
untouched by this feature.
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.database import Base
from db import models  # noqa: F401 — register models with Base
from db.models import Student, PerformanceVector
from fsm.dialogue_manager import handle_message
from fsm.states import FSMState


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


def _sid() -> str:
    return uuid.uuid4().hex + uuid.uuid4().hex


def _seed_named_student(db, sid: str, channel: str = "ussd", name: str = "Tester") -> None:
    db.add(Student(
        student_id=sid,
        channel=channel,
        name=name,
        registered_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc),
    ))
    db.commit()


def _to_subject_menu(db, sid: str, channel: str = "ussd"):
    """Drive a named student to SUBJECT_SELECTION via the greeting reset path."""
    return handle_message(db, sid, channel, "hi")


class TestUSSDReportOption:
    def test_menu_screen_advertises_option_5(self, db):
        sid = _sid()
        _seed_named_student(db, sid)
        result = _to_subject_menu(db, sid)
        assert result.new_state == FSMState.SUBJECT_SELECTION
        assert "5. My Report" in result.response

    def test_report_with_history_is_formatted_correctly(self, db):
        sid = _sid()
        _seed_named_student(db, sid)
        _to_subject_menu(db, sid)

        db.add(PerformanceVector(
            student_id=sid, subject="maths", topic="algebra",
            difficulty="easy", attempts=12, correct=9,
        ))
        db.add(PerformanceVector(
            student_id=sid, subject="english", topic="grammar",
            difficulty="easy", attempts=10, correct=6,
        ))
        db.commit()

        result = handle_message(db, sid, "ussd", "5")

        assert result.new_state == FSMState.SUBJECT_SELECTION  # not a dead end
        assert "Your Report:" in result.response
        assert "Total Qs: 22" in result.response
        assert "Accuracy: 68%" in result.response  # 15/22 -> 68.2 -> 68
        assert "Maths: 12 (75%)" in result.response
        assert "English: 10 (60%)" in result.response
        # Short "back" line, not the full re-printed subject menu.
        assert result.response.endswith("0. Back to Menu")
        assert "1. Maths" not in result.response

    def test_report_with_no_history_shows_graceful_message(self, db):
        sid = _sid()
        _seed_named_student(db, sid)
        _to_subject_menu(db, sid)

        result = handle_message(db, sid, "ussd", "5")

        assert result.new_state == FSMState.SUBJECT_SELECTION
        assert "You have not answered any questions yet" in result.response
        assert "Your Report:" not in result.response
        assert result.response.endswith("0. Back to Menu")
        assert "1. Maths" not in result.response

    def test_zero_from_report_screen_returns_to_subject_menu(self, db):
        """Sending '0' after the report must behave like MENU (re-show the
        subject list), not like STOP (end the session)."""
        sid = _sid()
        _seed_named_student(db, sid)
        _to_subject_menu(db, sid)
        handle_message(db, sid, "ussd", "5")  # view the report first

        result = handle_message(db, sid, "ussd", "0")

        assert result.new_state == FSMState.SUBJECT_SELECTION
        assert result.end_session is False
        assert "1. Maths" in result.response
        assert "5. My Report" in result.response

    def test_report_screen_fits_single_ussd_screen_without_pagination(self, db):
        sid = _sid()
        _seed_named_student(db, sid)
        _to_subject_menu(db, sid)

        for i, subj in enumerate(["maths", "english", "science", "social_studies"]):
            db.add(PerformanceVector(
                student_id=sid, subject=subj, topic=f"topic{i}",
                difficulty="easy", attempts=10 + i, correct=5 + i,
            ))
        db.commit()

        result = handle_message(db, sid, "ussd", "5")

        # With the full menu no longer re-appended, even a complete
        # 4-subject report fits in a single ~150-char USSD chunk — no
        # "99. More" pagination needed, and no mid-menu cut.
        assert len(result.response) <= 160
        assert "99. More" not in result.response
        assert result.response.endswith("0. Back to Menu")

    def test_report_option_does_not_affect_whatsapp(self, db):
        """The report shortcut is USSD-only; WhatsApp '5' stays an invalid subject, unchanged."""
        sid = _sid()
        _seed_named_student(db, sid, channel="whatsapp")
        handle_message(db, sid, "whatsapp", "hi")

        result = handle_message(db, sid, "whatsapp", "5")

        assert "Your Report:" not in result.response
        assert result.new_state == FSMState.SUBJECT_SELECTION
