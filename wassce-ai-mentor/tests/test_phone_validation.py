"""
tests/test_phone_validation.py — Regression coverage for the phone-number
input-validation fix (Twilio can occasionally deliver a non-numeric "From"
identifier for WhatsApp messages instead of a real E.164 number — confirmed
in production logs, e.g. "whatsapp:GH.2142378006405521"). Covers:

  - The WhatsApp webhook only persists Student.phone_number when it passes
    is_valid_phone(), end to end through the real endpoint.
  - Downstream dashboard behaviour for a student left with phone_number=None
    is already correct and stays correct: teacher view renders "unknown",
    student-by-phone lookup 404s (fails safe instead of matching garbage).
"""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.main import app
from api.routes import dashboard
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
    def test_invalid_from_value_is_not_stored(self, client, db_session):
        response = client.post("/webhook/whatsapp", data={"From": BAD_FROM, "Body": "Hi"})
        assert response.status_code == 200

        student = db_session.get(Student, phone_to_student_id(BAD_FROM))
        assert student is not None  # the interaction itself is still recorded
        assert student.phone_number is None  # but the bad identifier is rejected

    def test_valid_from_value_is_stored(self, client, db_session):
        response = client.post("/webhook/whatsapp", data={"From": GOOD_FROM, "Body": "Hi"})
        assert response.status_code == 200

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
