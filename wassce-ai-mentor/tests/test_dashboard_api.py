"""
tests/test_dashboard_api.py — Tests for the React dashboard JSON API
(api/routes/dashboard.py): phone normalisation, student/teacher endpoints,
auth, and rate limiting.
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
from db.models import PerformanceVector, Student
from utils.phone import normalise_phone, phone_to_student_id

PHONE_FORMATS = ["0531850867", "531850867", "+233531850867", "233531850867"]


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


def _make_student(db, raw_phone: str, name: str, channel: str = "whatsapp") -> str:
    student_id = phone_to_student_id(raw_phone)
    e164 = normalise_phone(raw_phone)
    db.add(Student(
        student_id=student_id,
        channel=channel,
        phone_number=f"whatsapp:{e164}",
        name=name,
        last_seen_at=datetime.utcnow(),
    ))
    db.commit()
    return student_id


# ── Phone normalisation ─────────────────────────────────────────────────────

def test_phone_normalization():
    normalised = {normalise_phone(f) for f in PHONE_FORMATS}
    assert normalised == {"+233531850867"}


def test_phone_normalization_yields_same_student_id():
    ids = {phone_to_student_id(f) for f in PHONE_FORMATS}
    assert len(ids) == 1


# ── Student endpoint ─────────────────────────────────────────────────────────

def test_student_endpoint_returns_own_data_only(client, db_session):
    _make_student(db_session, "0531850867", "Ama")
    other_id = _make_student(db_session, "0201234567", "Kojo")

    db_session.add(PerformanceVector(
        student_id=phone_to_student_id("0531850867"),
        subject="maths", topic="algebra", difficulty="easy",
        attempts=10, correct=7,
    ))
    db_session.add(PerformanceVector(
        student_id=other_id, subject="english", topic="grammar",
        difficulty="easy", attempts=4, correct=1,
    ))
    db_session.commit()

    response = client.get("/api/dashboard/student/0531850867")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Ama"
    assert data["total_questions"] == 10
    assert data["overall_accuracy"] == 70.0
    assert [s["subject"] for s in data["by_subject"]] == ["maths"]
    assert "Kojo" not in str(data)


def test_student_endpoint_404_for_unknown_number(client):
    response = client.get("/api/dashboard/student/0500000000")
    assert response.status_code == 404
    assert response.json() == {"error": "No student found with that number"}


def test_rate_limit_on_student_endpoint(client):
    for _ in range(dashboard.RATE_LIMIT_REQUESTS):
        response = client.get("/api/dashboard/student/0500000001")
        assert response.status_code == 404
    response = client.get("/api/dashboard/student/0500000001")
    assert response.status_code == 429


# ── Teacher endpoints ────────────────────────────────────────────────────────

def test_teacher_endpoint_requires_password(client):
    response = client.get("/api/dashboard/teacher/overview")
    assert response.status_code == 403


def test_teacher_endpoint_wrong_password_403(client):
    response = client.get(
        "/api/dashboard/teacher/overview",
        headers={"X-Dashboard-Password": "definitely-wrong-password"},
    )
    assert response.status_code == 403


def test_teacher_overview_returns_all_students(client, db_session):
    _make_student(db_session, "0531850867", "Ama")
    _make_student(db_session, "0201234567", "Kojo")
    password = get_settings().dashboard_password

    response = client.get(
        "/api/dashboard/teacher/overview",
        headers={"X-Dashboard-Password": password},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total_students"] == 2
    assert len(data["students"]) == 2


def test_teacher_student_drill_down_matches_student_shape(client, db_session):
    _make_student(db_session, "0531850867", "Ama")
    db_session.add(PerformanceVector(
        student_id=phone_to_student_id("0531850867"),
        subject="maths", topic="algebra", difficulty="easy",
        attempts=5, correct=5,
    ))
    db_session.commit()
    password = get_settings().dashboard_password

    response = client.get(
        "/api/dashboard/teacher/student/0531850867",
        headers={"X-Dashboard-Password": password},
    )
    assert response.status_code == 200
    assert response.json()["overall_accuracy"] == 100.0


def test_teacher_student_wrong_password_403(client, db_session):
    _make_student(db_session, "0531850867", "Ama")
    response = client.get(
        "/api/dashboard/teacher/student/0531850867",
        headers={"X-Dashboard-Password": "definitely-wrong-password"},
    )
    assert response.status_code == 403
