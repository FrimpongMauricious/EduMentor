"""
tests/test_name_capture.py — Tests for first-time user name registration.

Feature: First-time WhatsApp and USSD users are asked for their name.
         The name is stored in Student.name and used in personalised greetings
         and study reminder messages.
"""
import uuid
import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.database import Base
from db import models  # noqa: F401 — register models with Base
from fsm.dialogue_manager import handle_message
from fsm.states import FSMState


# ─── In-memory test DB fixture ────────────────────────────────────────────────

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def _sid() -> str:
    return uuid.uuid4().hex + uuid.uuid4().hex


def _seed_name(db, sid: str, channel: str = "whatsapp", name: str = "Tester") -> None:
    from db.models import Student
    from datetime import datetime, timezone
    existing = db.get(Student, sid)
    if existing is None:
        student = Student(
            student_id=sid,
            channel=channel,
            name=name,
            registered_at=datetime.now(timezone.utc),
            last_seen_at=datetime.now(timezone.utc),
        )
        db.add(student)
        db.commit()
    else:
        existing.name = name
        db.commit()


# ===========================================================================
# NAME CAPTURE FSM FLOW
# ===========================================================================

class TestNameCapture:
    """First-time user name registration on WhatsApp and USSD."""

    def test_first_time_whatsapp_user_gets_name_prompt(self, db):
        sid = _sid()
        result = handle_message(db, sid, "whatsapp", "Hi")
        assert result.new_state == FSMState.NAME_ENTRY
        assert "name" in result.response.lower()

    def test_first_time_ussd_user_gets_name_prompt(self, db):
        sid = _sid()
        result = handle_message(db, sid, "ussd", "")
        assert result.new_state == FSMState.NAME_ENTRY
        assert "name" in result.response.lower()

    def test_valid_name_accepted_whatsapp(self, db):
        sid = _sid()
        handle_message(db, sid, "whatsapp", "Hi")   # → NAME_ENTRY
        result = handle_message(db, sid, "whatsapp", "Mauricious")
        assert result.new_state == FSMState.SUBJECT_SELECTION
        assert "Mauricious" in result.response
        assert "Hi Mauricious" in result.response

    def test_valid_name_accepted_ussd(self, db):
        sid = _sid()
        handle_message(db, sid, "ussd", "")         # → NAME_ENTRY
        result = handle_message(db, sid, "ussd", "Mauricious")
        assert result.new_state == FSMState.SUBJECT_SELECTION
        assert "Mauricious" in result.response

    def test_name_stored_in_db(self, db):
        from db.models import Student
        sid = _sid()
        handle_message(db, sid, "whatsapp", "Hi")
        handle_message(db, sid, "whatsapp", "Abena")
        student = db.query(Student).filter(Student.student_id == sid).first()
        assert student is not None
        assert student.name == "Abena"

    def test_name_is_title_cased(self, db):
        from db.models import Student
        sid = _sid()
        handle_message(db, sid, "whatsapp", "Hi")
        handle_message(db, sid, "whatsapp", "kwame mensah")
        student = db.query(Student).filter(Student.student_id == sid).first()
        assert student.name == "Kwame Mensah"

    def test_name_too_short_rejected(self, db):
        sid = _sid()
        handle_message(db, sid, "whatsapp", "Hi")
        result = handle_message(db, sid, "whatsapp", "A")
        assert result.new_state == FSMState.NAME_ENTRY

    def test_empty_name_rejected(self, db):
        sid = _sid()
        handle_message(db, sid, "whatsapp", "Hi")
        result = handle_message(db, sid, "whatsapp", "")
        assert result.new_state == FSMState.NAME_ENTRY

    def test_numeric_name_rejected(self, db):
        sid = _sid()
        handle_message(db, sid, "whatsapp", "Hi")
        result = handle_message(db, sid, "whatsapp", "123")
        assert result.new_state == FSMState.NAME_ENTRY

    def test_number_1_rejected_as_name(self, db):
        sid = _sid()
        handle_message(db, sid, "whatsapp", "Hi")
        result = handle_message(db, sid, "whatsapp", "1")
        assert result.new_state == FSMState.NAME_ENTRY

    def test_menu_command_rejected_as_name(self, db):
        sid = _sid()
        handle_message(db, sid, "whatsapp", "Hi")
        result = handle_message(db, sid, "whatsapp", "MENU")
        assert result.new_state == FSMState.NAME_ENTRY

    def test_stop_command_rejected_as_name(self, db):
        sid = _sid()
        handle_message(db, sid, "whatsapp", "Hi")
        result = handle_message(db, sid, "whatsapp", "STOP")
        assert result.new_state == FSMState.NAME_ENTRY

    def test_help_command_rejected_as_name(self, db):
        sid = _sid()
        handle_message(db, sid, "whatsapp", "Hi")
        result = handle_message(db, sid, "whatsapp", "HELP")
        assert result.new_state == FSMState.NAME_ENTRY

    def test_hi_rejected_as_name(self, db):
        sid = _sid()
        handle_message(db, sid, "whatsapp", "Hi")
        result = handle_message(db, sid, "whatsapp", "Hi")
        assert result.new_state == FSMState.NAME_ENTRY

    def test_returning_user_whatsapp_sees_welcome_back(self, db):
        sid = _sid()
        _seed_name(db, sid, "whatsapp", "Mauricious")
        result = handle_message(db, sid, "whatsapp", "Hi")
        assert result.new_state == FSMState.SUBJECT_SELECTION
        assert "Welcome back" in result.response
        assert "Mauricious" in result.response

    def test_returning_user_ussd_sees_personalised_greeting(self, db):
        sid = _sid()
        _seed_name(db, sid, "ussd", "Abena")
        result = handle_message(db, sid, "ussd", "")
        assert result.new_state == FSMState.SUBJECT_SELECTION
        assert "Abena" in result.response

    def test_name_persists_across_new_sessions(self, db):
        sid = _sid()
        handle_message(db, sid, "whatsapp", "Hi")    # → NAME_ENTRY
        handle_message(db, sid, "whatsapp", "Kofi")  # → SUBJECT_SELECTION

        # Expire the active session so a new one is created
        from db.models import SessionRow
        session = db.query(SessionRow).filter(SessionRow.student_id == sid).first()
        session.is_expired = True
        db.commit()

        result = handle_message(db, sid, "whatsapp", "Hi")
        # Returning user → no name prompt
        assert result.new_state == FSMState.SUBJECT_SELECTION
        assert "Kofi" in result.response

    def test_name_not_asked_twice(self, db):
        sid = _sid()
        handle_message(db, sid, "whatsapp", "Hi")    # → NAME_ENTRY
        handle_message(db, sid, "whatsapp", "Ama")   # name saved → SUBJECT_SELECTION
        result = handle_message(db, sid, "whatsapp", "MENU")  # → SUBJECT_SELECTION
        result2 = handle_message(db, sid, "whatsapp", "STOP")  # session ended
        # New session for same student
        result3 = handle_message(db, sid, "whatsapp", "Hi")
        assert result3.new_state == FSMState.SUBJECT_SELECTION  # no name prompt!

    def test_ussd_name_prompt_fits_screen(self):
        from fsm.messages import name_prompt
        msg = name_prompt("ussd")
        assert len(msg) <= 160

    def test_ussd_name_invalid_fits_screen(self):
        from fsm.messages import name_invalid
        msg = name_invalid("ussd")
        assert len(msg) <= 160

    def test_ussd_name_accepted_menu_fits_screen(self):
        from fsm.messages import name_accepted_with_menu
        msg = name_accepted_with_menu("Kwame", "ussd")
        assert len(msg) <= 160

    def test_ussd_welcome_back_fits_screen(self):
        from fsm.messages import welcome_back
        msg = welcome_back("Ama", "ussd")
        assert len(msg) <= 160

    def test_whatsapp_name_prompt_not_paginated(self):
        from fsm.messages import name_prompt
        msg = name_prompt("whatsapp")
        assert "name" in msg.lower()

    def test_whatsapp_welcome_back_contains_subject_menu(self):
        from fsm.messages import welcome_back
        msg = welcome_back("Kofi", "whatsapp")
        assert "Core Mathematics" in msg
        assert "English Language" in msg
        assert "Integrated Science" in msg
        assert "Social Studies" in msg

    def test_student_model_has_name_field(self):
        from db.models import Student
        assert hasattr(Student, "name"), "Student model must have a name column"

    def test_full_ussd_name_capture_flow_to_question(self, db):
        """End-to-end USSD: name → subject → question."""
        sid = _sid()
        result1 = handle_message(db, sid, "ussd", "")
        assert result1.new_state == FSMState.NAME_ENTRY

        result2 = handle_message(db, sid, "ussd", "Ama")
        assert result2.new_state == FSMState.SUBJECT_SELECTION
        assert "Ama" in result2.response

        result3 = handle_message(db, sid, "ussd", "1")  # Maths → QUESTION_DELIVERY
        assert result3.new_state == FSMState.QUESTION_DELIVERY
        assert result3.question_id is not None
        assert result3.question_id.startswith("MATH-")

    def test_full_whatsapp_name_capture_flow_to_question(self, db):
        """End-to-end WhatsApp: name → subject → type → question."""
        sid = _sid()
        result1 = handle_message(db, sid, "whatsapp", "Hi")
        assert result1.new_state == FSMState.NAME_ENTRY

        result2 = handle_message(db, sid, "whatsapp", "Kwame Asante")
        assert result2.new_state == FSMState.SUBJECT_SELECTION
        assert "Kwame Asante" in result2.response

        result3 = handle_message(db, sid, "whatsapp", "1")  # Maths → type prompt
        assert result3.new_state == FSMState.QUESTION_TYPE_SELECTION

        result4 = handle_message(db, sid, "whatsapp", "1")  # MCQ → question
        assert result4.new_state == FSMState.QUESTION_DELIVERY
        assert result4.question_id is not None


# ===========================================================================
# BUG FIXES: NULL-NAME GUARD + GREETING RESET + SPACE-INSENSITIVE MCQ
# ===========================================================================

class TestBugFixes:
    """Tests covering the three bug fixes."""

    def test_existing_user_with_null_name_gets_prompted(self, db):
        """Bug 1: pre-deployment users (name=NULL) must be prompted regardless of state."""
        from db.models import Student
        from datetime import datetime, timezone
        sid = _sid()
        # Simulate a user record created before the name feature was deployed
        student = Student(
            student_id=sid,
            channel="whatsapp",
            name=None,
            registered_at=datetime.now(timezone.utc),
            last_seen_at=datetime.now(timezone.utc),
        )
        db.add(student)
        db.commit()

        result = handle_message(db, sid, "whatsapp", "hi")
        assert result.new_state == FSMState.NAME_ENTRY
        assert "name" in result.response.lower()

    def test_existing_user_null_name_gets_prompted_non_greeting(self, db):
        """Bug 1: null-name users are redirected to NAME_ENTRY even on non-greeting msgs."""
        from db.models import Student, SessionRow
        from datetime import datetime, timezone
        sid = _sid()
        student = Student(
            student_id=sid,
            channel="whatsapp",
            name=None,
            registered_at=datetime.now(timezone.utc),
            last_seen_at=datetime.now(timezone.utc),
        )
        db.add(student)
        db.commit()

        # Manually put session in SUBJECT_SELECTION (simulating stale pre-deployment state)
        handle_message(db, sid, "whatsapp", "")  # creates session in NAME_ENTRY via null guard
        session = db.query(SessionRow).filter(SessionRow.student_id == sid).first()
        session.fsm_state = "SUBJECT_SELECTION"
        db.commit()

        # A non-greeting message should still trigger name prompt
        result = handle_message(db, sid, "whatsapp", "Maths")
        assert result.new_state == FSMState.NAME_ENTRY
        assert "name" in result.response.lower()

    def test_greeting_resets_state_from_explanation(self, db):
        """Bug 2: sending 'hi' from EXPLANATION state resets to subject selection."""
        sid = _sid()
        _seed_name(db, sid, "whatsapp", "Kwame")
        handle_message(db, sid, "whatsapp", "Hi")   # → SUBJECT_SELECTION
        handle_message(db, sid, "whatsapp", "1")    # → QUESTION_TYPE_SELECTION
        handle_message(db, sid, "whatsapp", "1")    # → QUESTION_DELIVERY
        handle_message(db, sid, "whatsapp", "A")    # → EXPLANATION

        result = handle_message(db, sid, "whatsapp", "hi")
        assert result.new_state == FSMState.SUBJECT_SELECTION
        assert "Kwame" in result.response

    def test_greeting_resets_state_from_question_delivery(self, db):
        """Bug 2: 'hello' mid-question returns to subject selection."""
        sid = _sid()
        _seed_name(db, sid, "whatsapp", "Ama")
        handle_message(db, sid, "whatsapp", "Hi")
        handle_message(db, sid, "whatsapp", "1")
        handle_message(db, sid, "whatsapp", "1")    # → QUESTION_DELIVERY

        result = handle_message(db, sid, "whatsapp", "hello")
        assert result.new_state == FSMState.SUBJECT_SELECTION
        assert "Ama" in result.response

    def test_greeting_variants_all_reset(self, db):
        """Bug 2: 'hey', 'start', 'begin', 'restart' all trigger the reset."""
        for greeting in ("hey", "start", "begin", "restart"):
            sid = _sid()
            _seed_name(db, sid, "whatsapp", "Kofi")
            # Get into QUESTION_DELIVERY
            handle_message(db, sid, "whatsapp", "Hi")
            handle_message(db, sid, "whatsapp", "1")
            handle_message(db, sid, "whatsapp", "1")
            result = handle_message(db, sid, "whatsapp", greeting)
            assert result.new_state == FSMState.SUBJECT_SELECTION, (
                f"Greeting '{greeting}' did not reset state"
            )

    def test_greeting_case_insensitive_reset(self, db):
        """Bug 2: 'HI', 'Hello', 'HELLO' should all trigger the reset."""
        for greeting in ("HI", "Hello", "HELLO", "Hey"):
            sid = _sid()
            _seed_name(db, sid, "whatsapp", "Abena")
            handle_message(db, sid, "whatsapp", "Hi")
            handle_message(db, sid, "whatsapp", "1")
            handle_message(db, sid, "whatsapp", "1")   # QUESTION_DELIVERY
            result = handle_message(db, sid, "whatsapp", greeting)
            assert result.new_state == FSMState.SUBJECT_SELECTION

    def test_700N_matches_700_N(self):
        """MCQ space-insensitive: '700N' should match correct answer '700 N'."""
        from rag.grader import grade_mcq
        result = grade_mcq("700N", "C. 700 N")
        assert result["is_correct"] is True
        assert result["score"] == 100

    def test_10kg_matches_10_kg(self):
        """MCQ space-insensitive: '10kg' should match '10 kg'."""
        from rag.grader import grade_mcq
        result = grade_mcq("10kg", "B. 10 kg")
        assert result["is_correct"] is True
        assert result["score"] == 100

    def test_space_insensitive_does_not_over_match(self):
        """Space-insensitive matching must not create false positives."""
        from rag.grader import grade_mcq
        result = grade_mcq("700N", "C. 800 N")
        assert result["is_correct"] is False


# ===========================================================================
# PERSONALISED REMINDERS
# ===========================================================================

class TestPersonalisedReminders:
    """Verify that reminders include student names when available."""

    def test_get_whatsapp_users_with_names_returns_list(self):
        from api.reminders import _get_whatsapp_users_with_names
        users = _get_whatsapp_users_with_names()
        assert isinstance(users, list)

    def test_get_whatsapp_users_with_names_dict_shape(self):
        from api.reminders import _get_whatsapp_users_with_names
        users = _get_whatsapp_users_with_names()
        for u in users:
            assert "phone" in u
            assert "name" in u
