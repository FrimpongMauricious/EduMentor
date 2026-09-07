"""
tests/test_leaderboard.py — Coverage for the exam-prep leaderboard:
- GET /api/dashboard/leaderboard (student/guardian, masked)
- GET /api/dashboard/teacher/leaderboard (teacher, full names)

Ranking metric: the Wilson score interval lower bound on accuracy (95%
confidence) — see api/routes/dashboard.py::_wilson_lower_bound for the
full rationale. Fairness check: a student with a tiny perfect record
(3/3) must NOT outrank one with a large near-perfect record (95/100).
"""
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.main import app
from api.routes import dashboard as dash
from config import get_settings
from db.database import Base, get_db
from db import models  # noqa: F401 — register models with Base
from db.models import Student, PerformanceVector
from utils.phone import phone_to_student_id


def _sid() -> str:
    return uuid.uuid4().hex + uuid.uuid4().hex


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
    dash._request_log.clear()
    yield
    dash._request_log.clear()


def _add_student(db, name, phone_number=None, student_id=None):
    sid = student_id or _sid()
    db.add(Student(
        student_id=sid, channel="whatsapp", name=name, phone_number=phone_number,
        registered_at=datetime.now(timezone.utc), last_seen_at=datetime.now(timezone.utc),
    ))
    db.commit()
    return sid


def _add_attempts(db, sid, subject, topic, attempts, correct, difficulty="easy"):
    db.add(PerformanceVector(
        student_id=sid, subject=subject, topic=topic,
        difficulty=difficulty, attempts=attempts, correct=correct,
    ))
    db.commit()


class TestWilsonScoreFormula:
    """Unit-level: the formula itself must produce the documented fairness
    property, independent of the HTTP layer."""

    def test_large_near_perfect_record_outranks_tiny_perfect_record(self):
        small_perfect = dash._wilson_lower_bound(3, 3)
        large_near_perfect = dash._wilson_lower_bound(95, 100)
        # Concrete, documented figures (as percentages, 1 d.p.):
        assert round(small_perfect * 100, 1) == 43.8
        assert round(large_near_perfect * 100, 1) == 88.8
        assert large_near_perfect > small_perfect

    def test_more_attempts_at_equal_high_accuracy_scores_higher(self):
        # Same 80% raw accuracy, but more evidence should narrow the
        # confidence interval and push the lower bound up.
        few = dash._wilson_lower_bound(8, 10)
        many = dash._wilson_lower_bound(80, 100)
        assert many > few

    def test_zero_attempts_is_defined_as_zero_not_a_crash(self):
        assert dash._wilson_lower_bound(0, 0) == 0.0


class TestComputeLeaderboardExclusionAndTiebreak:
    def test_zero_attempt_students_are_excluded(self, db_session):
        active = _add_student(db_session, "Ama")
        _add_attempts(db_session, active, "maths", "algebra", 5, 4)
        _add_student(db_session, "Zero Attempts")  # never answers anything

        rows = dash._compute_leaderboard(db_session, subject=None)
        assert len(rows) == 1
        assert rows[0]["name"] == "Ama"

    def test_subject_scope_excludes_students_with_only_other_subjects(self, db_session):
        maths_only = _add_student(db_session, "Kofi")
        _add_attempts(db_session, maths_only, "maths", "algebra", 10, 8)
        english_only = _add_student(db_session, "Abena")
        _add_attempts(db_session, english_only, "english", "grammar", 10, 9)

        rows = dash._compute_leaderboard(db_session, subject="maths")
        names = {r["name"] for r in rows}
        assert names == {"Kofi"}

    def test_tiebreak_by_attempts_then_name(self, db_session, monkeypatch):
        # Force identical scores so the tiebreak logic is what's actually tested.
        monkeypatch.setattr(dash, "_wilson_lower_bound", lambda correct, attempts: 0.5)

        low_attempts = _add_student(db_session, "Zainab")
        _add_attempts(db_session, low_attempts, "maths", "algebra", 5, 3)
        high_attempts = _add_student(db_session, "Yaw")
        _add_attempts(db_session, high_attempts, "maths", "algebra", 20, 12)
        tied_name_a = _add_student(db_session, "Bea")
        _add_attempts(db_session, tied_name_a, "maths", "algebra", 20, 12)

        rows = dash._compute_leaderboard(db_session, subject=None)
        # More attempts wins the tie; among equal attempts, alphabetical by name.
        assert [r["name"] for r in rows] == ["Bea", "Yaw", "Zainab"]


class TestPublicLeaderboardEndpoint:
    def test_own_row_unmasked_others_first_name_only(self, client, db_session):
        me_phone = "whatsapp:+233241111111"
        me = _add_student(db_session, "Ama Boateng", phone_number=me_phone,
                           student_id=phone_to_student_id(me_phone))
        _add_attempts(db_session, me, "maths", "algebra", 10, 8)

        other = _add_student(db_session, "Kwame Mensah")
        _add_attempts(db_session, other, "maths", "algebra", 20, 19)

        response = client.get(
            "/api/dashboard/leaderboard",
            params={"phone": "0241111111"},  # local format; normalises to the same E.164
        )
        assert response.status_code == 200
        body = response.json()

        by_name = {r["name"]: r for r in body["rankings"]}
        assert "Ama Boateng" in by_name  # own row: full name
        assert by_name["Ama Boateng"]["is_you"] is True
        assert "Kwame" in by_name  # other row: first name only
        assert "Kwame Mensah" not in by_name
        assert by_name["Kwame"]["is_you"] is False

        assert body["viewer"]["has_data"] is True
        assert body["viewer"]["name"] == "Ama Boateng"

    def test_duplicate_first_names_disambiguated_with_last_initial(self, client, db_session):
        a = _add_student(db_session, "Ama Owusu")
        _add_attempts(db_session, a, "maths", "algebra", 10, 9)
        b = _add_student(db_session, "Ama Boateng")
        _add_attempts(db_session, b, "maths", "algebra", 15, 10)

        response = client.get("/api/dashboard/leaderboard")
        assert response.status_code == 200
        names = {r["name"] for r in response.json()["rankings"]}
        assert names == {"Ama O.", "Ama B."}

    def test_viewer_with_zero_attempts_in_scope_reports_no_data_not_a_crash(self, client, db_session):
        me_phone = "whatsapp:+233241111111"
        me = _add_student(db_session, "Ama", phone_number=me_phone,
                           student_id=phone_to_student_id(me_phone))
        _add_attempts(db_session, me, "english", "grammar", 10, 9)  # no maths attempts

        response = client.get(
            "/api/dashboard/leaderboard",
            params={"subject": "maths", "phone": "0241111111"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["viewer"] == {"has_data": False}
        assert body["rankings"] == []  # nobody has maths attempts in this fixture

    def test_no_phone_given_returns_null_viewer(self, client, db_session):
        s = _add_student(db_session, "Ama")
        _add_attempts(db_session, s, "maths", "algebra", 5, 4)

        response = client.get("/api/dashboard/leaderboard")
        assert response.status_code == 200
        assert response.json()["viewer"] is None

    def test_subject_filter_scopes_the_response(self, client, db_session):
        s = _add_student(db_session, "Ama")
        _add_attempts(db_session, s, "maths", "algebra", 10, 8)
        _add_attempts(db_session, s, "english", "grammar", 10, 9)

        maths_only = client.get("/api/dashboard/leaderboard", params={"subject": "maths"})
        assert maths_only.status_code == 200
        assert maths_only.json()["rankings"][0]["attempts"] == 10
        assert maths_only.json()["rankings"][0]["accuracy"] == 80.0

        overall = client.get("/api/dashboard/leaderboard")
        assert overall.json()["rankings"][0]["attempts"] == 20  # 10 + 10

    def test_invalid_subject_rejected(self, client, db_session):
        response = client.get("/api/dashboard/leaderboard", params={"subject": "history"})
        assert response.status_code == 400


class TestTeacherLeaderboardEndpoint:
    def test_full_names_shown_no_masking(self, client, db_session):
        a = _add_student(db_session, "Ama Owusu")
        _add_attempts(db_session, a, "maths", "algebra", 10, 9)
        b = _add_student(db_session, "Ama Boateng")
        _add_attempts(db_session, b, "maths", "algebra", 15, 10)

        password = get_settings().dashboard_password
        response = client.get(
            "/api/dashboard/teacher/leaderboard",
            headers={"X-Dashboard-Password": password},
        )
        assert response.status_code == 200
        names = {r["name"] for r in response.json()["rankings"]}
        # Full names, unmasked, unlike the student/guardian endpoint's
        # "Ama O." / "Ama B." for the exact same underlying data.
        assert names == {"Ama Owusu", "Ama Boateng"}

    def test_wrong_password_403(self, client, db_session):
        response = client.get(
            "/api/dashboard/teacher/leaderboard",
            headers={"X-Dashboard-Password": "wrong"},
        )
        assert response.status_code == 403

    def test_teacher_leaderboard_has_no_viewer_concept(self, client, db_session):
        s = _add_student(db_session, "Ama")
        _add_attempts(db_session, s, "maths", "algebra", 5, 4)
        password = get_settings().dashboard_password
        response = client.get(
            "/api/dashboard/teacher/leaderboard",
            headers={"X-Dashboard-Password": password},
        )
        assert "viewer" not in response.json()
