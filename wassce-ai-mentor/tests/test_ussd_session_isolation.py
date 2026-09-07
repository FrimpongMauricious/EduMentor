"""
tests/test_ussd_session_isolation.py — Regression coverage for the USSD
call-isolation bug that nearly disrupted a live demo: a student dialing
in for a brand new USSD call would sometimes resume wherever their
PREVIOUS call left off (mid-question, past subject selection, etc.)
instead of seeing the initial subject-selection menu.

Root cause: fsm.dialogue_manager._get_or_create_session() decided
"same session vs new session" purely by elapsed time (the WhatsApp-
appropriate SESSION_TIMEOUT_MINUTES inactivity window), with no concept
of a USSD call boundary at all — api/routes/ussd.py read Africa's
Talking's per-call `sessionId` from the form but never passed it into
handle_message(), so it was logged and then completely discarded.
Since USSD sessions are call-scoped (a brand new dial-in gets a brand
new sessionId even seconds after the previous call ended), any redial
within the timeout window silently resumed the previous call's FSM
state.

Fix: handle_message()/_get_or_create_session() now accept an optional
ussd_session_id. When provided (i.e. the caller is the USSD webhook),
continuity is decided by comparing it to what's stored on the student's
current session — never by elapsed time. WhatsApp (ussd_session_id=None)
is completely unchanged.
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.database import Base
from db import models  # noqa: F401 — register models with Base
from db.models import SessionRow
from fsm.dialogue_manager import handle_message, _get_or_create_session
from fsm.states import FSMState


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


def _seed_named_student(db, sid: str, name: str = "Ama") -> None:
    from db.models import Student
    db.add(Student(
        student_id=sid, channel="ussd", name=name,
        registered_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc),
    ))
    db.commit()


def _current_session(db, sid: str) -> SessionRow:
    return (
        db.query(SessionRow)
        .filter(SessionRow.student_id == sid, SessionRow.is_expired == False)  # noqa: E712
        .order_by(SessionRow.last_active_at.desc())
        .first()
    )


class TestSameCallContinues:
    def test_same_session_id_continues_the_flow(self, db):
        sid = _sid()
        _seed_named_student(db, sid)
        call_id = "ATUid_call-1"

        # First request of the call: named user lands straight on the
        # subject menu (GREETING -> SUBJECT_SELECTION welcome-back).
        r1 = handle_message(db, sid, "ussd", "", ussd_session_id=call_id)
        assert r1.new_state == FSMState.SUBJECT_SELECTION

        # Second request, SAME call: picking "1" (Maths) must continue the
        # flow, not reset back to the subject menu.
        r2 = handle_message(db, sid, "ussd", "1", ussd_session_id=call_id)
        assert r2.new_state in (FSMState.QUESTION_TYPE_SELECTION, FSMState.QUESTION_DELIVERY)

        # Exactly one session row was used throughout — no silent reset.
        session = _current_session(db, sid)
        assert session.ussd_session_id == call_id

    def test_multi_step_flow_survives_several_same_session_requests(self, db):
        sid = _sid()
        _seed_named_student(db, sid)
        call_id = "ATUid_call-2"

        handle_message(db, sid, "ussd", "", ussd_session_id=call_id)      # -> SUBJECT_SELECTION
        handle_message(db, sid, "ussd", "1", ussd_session_id=call_id)     # -> Maths
        r3 = handle_message(db, sid, "ussd", "1", ussd_session_id=call_id)  # -> pick MCQ/question

        # Whichever exact state this lands on, it must not have been reset
        # back to SUBJECT_SELECTION or NAME_ENTRY mid-call.
        assert r3.new_state not in (FSMState.GREETING, FSMState.NAME_ENTRY)
        session = _current_session(db, sid)
        assert session.ussd_session_id == call_id


class TestNewCallResetsRegardlessOfElapsedTime:
    def test_different_session_id_resets_to_subject_menu_for_named_student(self, db):
        sid = _sid()
        _seed_named_student(db, sid)

        # Call 1: drive the student deep into the flow (subject picked).
        handle_message(db, sid, "ussd", "", ussd_session_id="call-A")
        handle_message(db, sid, "ussd", "1", ussd_session_id="call-A")
        mid_call_state = _current_session(db, sid).fsm_state
        assert mid_call_state not in (FSMState.GREETING.value, FSMState.SUBJECT_SELECTION.value)

        # Call 2: a DIFFERENT sessionId for the exact same student_id —
        # must start fresh at the subject menu, not resume mid-flow.
        r2 = handle_message(db, sid, "ussd", "", ussd_session_id="call-B")
        assert r2.new_state == FSMState.SUBJECT_SELECTION

        session = _current_session(db, sid)
        assert session.ussd_session_id == "call-B"

    def test_different_session_id_resets_to_name_entry_for_unnamed_student(self, db):
        sid = _sid()  # no name seeded — brand new student

        handle_message(db, sid, "ussd", "", ussd_session_id="call-A")
        r1_name = handle_message(db, sid, "ussd", "Kwame", ussd_session_id="call-A")
        assert r1_name.new_state == FSMState.SUBJECT_SELECTION  # name captured mid call-A

        # A second real call for a DIFFERENT (still nameless) student must
        # land on NAME_ENTRY again, not skip straight to subject selection.
        sid2 = _sid()
        r2 = handle_message(db, sid2, "ussd", "", ussd_session_id="call-C")
        assert r2.new_state == FSMState.NAME_ENTRY

    def test_reset_happens_even_with_almost_no_elapsed_time(self, db):
        """The defining property of this fix: a redial one second later is
        still a NEW call and must reset, because the decision is based on
        sessionId, never on how much time has passed."""
        sid = _sid()
        _seed_named_student(db, sid)

        handle_message(db, sid, "ussd", "", ussd_session_id="call-X")
        handle_message(db, sid, "ussd", "1", ussd_session_id="call-X")

        # Force last_active_at to "right now" — the smallest possible gap,
        # nowhere near the inactivity timeout — to prove elapsed time is
        # irrelevant to this decision.
        session = _current_session(db, sid)
        session.last_active_at = datetime.now(timezone.utc)
        db.commit()

        r2 = handle_message(db, sid, "ussd", "", ussd_session_id="call-Y")
        assert r2.new_state == FSMState.SUBJECT_SELECTION

    def test_no_stored_session_id_yet_is_treated_as_a_new_call(self, db):
        """A session created some other way (e.g. pre-migration legacy row
        with ussd_session_id=None) must never be silently resumed just
        because a sessionId happens to be falsy/missing — any mismatch,
        including None-vs-something, forces a reset."""
        sid = _sid()
        _seed_named_student(db, sid)

        legacy_session = _get_or_create_session(db, sid, ussd_session_id=None)
        legacy_session.fsm_state = FSMState.QUESTION_DELIVERY.value
        db.commit()
        assert legacy_session.ussd_session_id is None

        r = handle_message(db, sid, "ussd", "", ussd_session_id="call-fresh")
        assert r.new_state == FSMState.SUBJECT_SELECTION


class TestWhatsAppUnaffected:
    def test_whatsapp_session_still_continues_within_timeout(self, db):
        """Regression check: WhatsApp (no ussd_session_id) must keep using
        the original purely time-based continuity — completely untouched
        by this fix."""
        sid = _sid()
        _seed_named_student(db, sid, name="Kwame")

        r1 = handle_message(db, sid, "whatsapp", "hi")
        assert r1.new_state == FSMState.SUBJECT_SELECTION
        r2 = handle_message(db, sid, "whatsapp", "1")
        assert r2.new_state in (FSMState.QUESTION_TYPE_SELECTION, FSMState.QUESTION_DELIVERY)

        session = _current_session(db, sid)
        assert session.ussd_session_id is None  # never set for WhatsApp

    def test_whatsapp_session_row_never_gets_a_ussd_session_id(self, db):
        sid = _sid()
        _seed_named_student(db, sid, name="Ama")
        handle_message(db, sid, "whatsapp", "hi")
        session = _current_session(db, sid)
        assert session.ussd_session_id is None
