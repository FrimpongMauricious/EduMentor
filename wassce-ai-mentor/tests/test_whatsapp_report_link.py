"""
tests/test_whatsapp_report_link.py — WhatsApp "Get My Report" menu option
(option 5 from the subject-selection screen). Unlike the USSD "My Report"
feature (which renders a summary inline, screen-size being a constraint
there), WhatsApp just links to the existing web dashboard — no new
aggregation logic, no new API calls.

This file only covers the new, additive WhatsApp surface. Existing
WhatsApp navigation, question delivery, and grading are covered by
tests/test_fsm.py and tests/test_new_features.py and are untouched by
this feature.
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.database import Base
from db import models  # noqa: F401 — register models with Base
from db.models import Student
from fsm.dialogue_manager import handle_message
from fsm.states import FSMState
from fsm.messages import DASHBOARD_STUDENT_URL


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


def _seed_named_student(db, sid: str, phone_number: str | None = None, name: str = "Tester") -> None:
    db.add(Student(
        student_id=sid,
        channel="whatsapp",
        name=name,
        phone_number=phone_number,
        registered_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc),
    ))
    db.commit()


def _to_subject_menu(db, sid: str):
    return handle_message(db, sid, "whatsapp", "hi")


class TestWhatsAppReportLink:
    def test_menu_advertises_get_my_report(self, db):
        sid = _sid()
        _seed_named_student(db, sid)
        result = _to_subject_menu(db, sid)
        assert result.new_state == FSMState.SUBJECT_SELECTION
        assert "5. Get My Report (web)" in result.response

    def test_selecting_5_with_known_phone_returns_prefilled_link(self, db):
        sid = _sid()
        _seed_named_student(db, sid, phone_number="whatsapp:+233241234567")
        _to_subject_menu(db, sid)

        result = handle_message(db, sid, "whatsapp", "5")

        assert result.new_state == FSMState.SUBJECT_SELECTION  # not a dead end
        assert f"{DASHBOARD_STUDENT_URL}?phone=%2B233241234567" in result.response
        assert "Reply MENU to go back." in result.response

    def test_selecting_5_without_known_phone_returns_simple_link(self, db):
        sid = _sid()
        _seed_named_student(db, sid, phone_number=None)
        _to_subject_menu(db, sid)

        result = handle_message(db, sid, "whatsapp", "5")

        assert result.new_state == FSMState.SUBJECT_SELECTION
        assert result.response.startswith("View your performance report on our dashboard:")
        assert DASHBOARD_STUDENT_URL in result.response
        assert "?phone=" not in result.response
        assert "Enter your phone number there" in result.response
        assert "Reply MENU to go back." in result.response

    def test_selecting_5_with_invalid_stored_phone_falls_back_to_simple_link(self, db):
        """A non-numeric phone_number (e.g. rejected-but-somehow-present) must
        never be dropped into the URL — fail safe to the simple link instead."""
        sid = _sid()
        _seed_named_student(db, sid, phone_number="whatsapp:GH.2142378006405521")
        _to_subject_menu(db, sid)

        result = handle_message(db, sid, "whatsapp", "5")

        assert "?phone=" not in result.response
        assert DASHBOARD_STUDENT_URL in result.response

    def test_menu_still_navigable_after_report_link(self, db):
        """Sending MENU/NEXT-style commands after viewing the report link
        must work normally — the student isn't locked into a new state."""
        sid = _sid()
        _seed_named_student(db, sid, phone_number="whatsapp:+233241234567")
        _to_subject_menu(db, sid)
        handle_message(db, sid, "whatsapp", "5")

        result = handle_message(db, sid, "whatsapp", "1")  # pick Maths normally

        assert result.new_state in {FSMState.QUESTION_DELIVERY, FSMState.QUESTION_TYPE_SELECTION}

    def test_existing_subject_options_unaffected(self, db):
        sid = _sid()
        _seed_named_student(db, sid, phone_number="whatsapp:+233241234567")
        _to_subject_menu(db, sid)

        result = handle_message(db, sid, "whatsapp", "2")  # English

        assert result.new_state in {FSMState.QUESTION_DELIVERY, FSMState.QUESTION_TYPE_SELECTION}
        assert "Get My Report" not in result.response
