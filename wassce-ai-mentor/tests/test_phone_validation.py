"""
tests/test_phone_validation.py — Regression coverage for the phone-number
input-validation fixes (Twilio can occasionally deliver a non-numeric "From"
identifier for WhatsApp messages instead of a real E.164 number — confirmed
in production logs, e.g. "whatsapp:GH.2142378006405521"). Two separate fixes
are covered here:

  - 51a1c6b: don't *store* a bad "From" as Student.phone_number.
  - Follow-up root-cause fix: don't *hash* a bad "From" into a brand-new
    student_id at all. 51a1c6b alone still let api/routes/whatsapp.py derive
    student_id = sha256(normalise_phone(From)) from the garbled value before
    validating it, silently forking a second, permanently phoneless Student
    record for what could be an existing student's account glitching. The
    webhook now checks is_valid_phone(From) BEFORE deriving any student_id;
    an invalid From creates or touches no Student row at all and gets a
    generic decline reply instead.

Also covers downstream dashboard behaviour for a student left with
phone_number=None (from before either fix, or some other cause): teacher
view renders "unknown", student-by-phone lookup 404s (fails safe instead of
matching garbage) — already correct and confirmed to stay correct.
"""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.main import app
from api.routes import dashboard
from api.routes.whatsapp import UNRECOGNISED_SENDER_REPLY
from config import get_settings
from db.database import Base, get_db
from db.models import Student
from utils.phone import phone_to_student_id

BAD_FROM = "whatsapp:GH.2142378006405521"  # observed in production Twilio logs
GOOD_FROM = "whatsapp:+233241234567"


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    session = TestSession()

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield session
    finally:
        session.close()
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def client(db_session):
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    dashboard._request_log.clear()
    yield
    dashboard._request_log.clear()


# ── Webhook: validated write ────────────────────────────────────────────────

class TestWebhookPhoneValidation:
    def test_invalid_from_value_creates_no_student_identity(self, client, db_session):
        """
        Root-cause fix: an invalid From must never be hashed into a new
        student_id. This replaces a superseded version of this test that
        asserted a Student row WAS created for BAD_FROM (with phone_number
        left None) — that was asserting the bug itself (a second, permanently
        phoneless identity forked off a garbled Twilio value). The correct
        behaviour is that no identity is created or touched at all.
        """
        response = client.post("/webhook/whatsapp", data={"From": BAD_FROM, "Body": "Hi"})
        assert response.status_code == 200
        assert "Sorry" in response.text  # generic decline, not a real FSM reply

        student = db_session.get(Student, phone_to_student_id(BAD_FROM))
        assert student is None

    def test_valid_from_value_is_stored(self, client, db_session):
        response = client.post("/webhook/whatsapp", data={"From": GOOD_FROM, "Body": "Hi"})
        assert response.status_code == 200

        student = db_session.get(Student, phone_to_student_id(GOOD_FROM))
        assert student is not None
        assert student.phone_number == GOOD_FROM

    def test_valid_from_value_unaffected_by_the_new_guard(self, client, db_session):
        """Task 2 regression check: the validation guard is edge-case-only —
        a normal, valid sender goes through handle_message() exactly as
        before and gets a real FSM reply, not the generic decline message."""
        response = client.post(
            "/webhook/whatsapp",
            data={"From": "whatsapp:+233531850867", "Body": "Hi"},
        )
        assert response.status_code == 200
        assert UNRECOGNISED_SENDER_REPLY not in response.text

        student = db_session.get(Student, phone_to_student_id("whatsapp:+233531850867"))
        assert student is not None
        assert student.phone_number == "whatsapp:+233531850867"

    def test_student_registers_normally_after_a_prior_declined_bad_from(self, client, db_session):
        """A transient bad-From message leaves no trace under any identity,
        so it can never block that same person's registration: their next
        message with a valid From (whether Twilio recovers on their next
        send, or they simply retry) is handled as an ordinary first contact,
        with no leftover state from the declined attempt."""
        bad_response = client.post("/webhook/whatsapp", data={"From": BAD_FROM, "Body": "Hi"})
        assert bad_response.status_code == 200
        assert db_session.get(Student, phone_to_student_id(BAD_FROM)) is None

        good_response = client.post(
            "/webhook/whatsapp", data={"From": GOOD_FROM, "Body": "Hi"}
        )
        assert good_response.status_code == 200
        assert UNRECOGNISED_SENDER_REPLY not in good_response.text

        student = db_session.get(Student, phone_to_student_id(GOOD_FROM))
        assert student is not None
        assert student.phone_number == GOOD_FROM


# ── Downstream dashboard behaviour (verify, not change) ────────────────────

class TestDownstreamBehaviourForUnvalidatedStudent:
    def test_teacher_dashboard_renders_unknown_for_null_phone(self, client, db_session):
        db_session.add(Student(
            student_id=phone_to_student_id(BAD_FROM),
            channel="whatsapp",
            phone_number=None,
            name="Kwame",
            last_seen_at=datetime.utcnow(),
        ))
        db_session.commit()
        password = get_settings().dashboard_password

        response = client.get(
            "/api/dashboard/teacher/overview",
            headers={"X-Dashboard-Password": password},
        )
        assert response.status_code == 200
        rows = response.json()["students"]
        assert len(rows) == 1
        assert rows[0]["phone"] is None
        assert rows[0]["phone_masked"] == "unknown"

    def test_student_lookup_404s_when_no_valid_phone_was_stored(self, client, db_session):
        db_session.add(Student(
            student_id=phone_to_student_id(BAD_FROM),
            channel="whatsapp",
            phone_number=None,
            name="Kwame",
            last_seen_at=datetime.utcnow(),
        ))
        db_session.commit()

        # No real phone number was ever stored for this student, so there is
        # no number anyone could type on the dashboard that would find them —
        # this is the correct, fail-safe outcome, not a regression.
        response = client.get("/api/dashboard/student/0241234567")
        assert response.status_code == 404
        assert response.json() == {"error": "No student found with that number"}
