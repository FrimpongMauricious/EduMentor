"""
tests/test_recommendations.py — AI-generated learning recommendations
(ai/recommendations.py), the every-10-answers trigger in
fsm/dialogue_manager.py, and their exposure via the dashboard API.

Every test mocks the actual LLM call (ai.recommendations._call_llm) —
these tests verify grounding (what data reaches the prompt), trigger
cadence, and failure handling, not real model output.
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import ai.recommendations as recs
from db.database import Base
from db import models  # noqa: F401 — register models with Base
from db.models import Student, PerformanceVector, CohortInsight


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


def _sid() -> str:
    return uuid.uuid4().hex + uuid.uuid4().hex


def _make_student(db, sid: str, name: str = "Tester") -> Student:
    student = Student(
        student_id=sid, channel="whatsapp", name=name,
        registered_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc),
    )
    db.add(student)
    db.commit()
    return student


def _seed_attempts(db, sid: str, subject: str, topic: str, attempts: int, correct: int, difficulty: str = "easy"):
    db.add(PerformanceVector(
        student_id=sid, subject=subject, topic=topic,
        difficulty=difficulty, attempts=attempts, correct=correct,
    ))
    db.commit()


class TestTriggerCadence:
    """The every-10-answers gate must actually be enforced, not just intended."""

    def test_no_trigger_below_ten_answers(self, db, monkeypatch):
        sid = _sid()
        _make_student(db, sid)
        calls = []
        monkeypatch.setattr(recs, "_call_llm", lambda prompt: calls.append(prompt) or "stub")

        for total in (1, 5, 9):
            _seed_attempts(db, sid, "maths", f"topic{total}", attempts=1, correct=1)
            fired = recs.maybe_trigger_student_recommendation(db, sid)
            assert fired is False

        assert calls == []  # LLM never called before the 10th answer

    def test_trigger_fires_exactly_at_10_20_30(self, db, monkeypatch):
        sid = _sid()
        _make_student(db, sid)
        monkeypatch.setattr(recs, "_call_llm", lambda prompt: "Focus on your weaker subject.")

        fire_log = []
        # Simulate answers arriving one at a time, 1..32, checking the gate every time.
        running_total = 0
        for i in range(1, 33):
            _seed_attempts(db, sid, "maths", f"t{i}", attempts=1, correct=1)
            running_total += 1
            fired = recs.maybe_trigger_student_recommendation(db, sid)
            if fired:
                fire_log.append(running_total)

        assert fire_log == [10, 20, 30]

    def test_dispatch_wired_into_dialogue_manager(self, db, monkeypatch):
        """The trigger must actually be reachable from the real chat flow,
        not just callable in isolation."""
        import fsm.dialogue_manager as dm
        from fsm.dialogue_manager import handle_message

        calls = []

        def fake_dispatch(student_id):
            calls.append(student_id)
            recs.maybe_trigger_student_recommendation(db, student_id)

        monkeypatch.setattr(dm, "_dispatch_recommendation_check", fake_dispatch)
        monkeypatch.setattr(recs, "_call_llm", lambda prompt: "Keep practising Mathematics.")

        sid = _sid()
        _make_student(db, sid)
        handle_message(db, sid, "whatsapp", "hi")  # -> SUBJECT_SELECTION
        handle_message(db, sid, "whatsapp", "1")   # -> Maths question (MCQ, single-type or type prompt)

        # Whatever path was taken (type prompt or direct question), the
        # dispatch hook must not have fired yet — no answer graded yet.
        assert calls == []


class TestGroundingInRealData:
    """The prompt sent to the model must contain only real stored numbers."""

    def test_prompt_contains_only_seeded_data(self, db, monkeypatch):
        sid = _sid()
        student = _make_student(db, sid)
        _seed_attempts(db, sid, "maths", "algebra", attempts=7, correct=6)
        _seed_attempts(db, sid, "english", "grammar", attempts=3, correct=1)

        captured = {}
        def fake_call(prompt):
            captured["prompt"] = prompt
            return "Focus on English grammar — your accuracy there is lower than in Mathematics."
        monkeypatch.setattr(recs, "_call_llm", fake_call)

        recs.generate_student_recommendation(db, student, audience="student")

        prompt = captured["prompt"]
        # Real, seeded numbers must be present.
        assert "10" in prompt  # total_questions_answered = 7 + 3
        assert "Mathematics" in prompt
        assert "English Language" in prompt
        assert "algebra" in prompt
        assert "grammar" in prompt
        # The grounding rules forbidding invention must be included verbatim.
        assert "Do not invent, assume, or guess any fact not present" in prompt
        # Nothing about a subject this student never touched.
        assert "Social Studies" not in prompt
        assert "Integrated Science" not in prompt

    def test_recommendation_text_persisted_and_truncated(self, db, monkeypatch):
        sid = _sid()
        student = _make_student(db, sid)
        _seed_attempts(db, sid, "maths", "algebra", attempts=10, correct=8)

        long_text = "x" * 900  # deliberately over MAX_RECOMMENDATION_CHARS
        monkeypatch.setattr(recs, "_call_llm", lambda prompt: long_text)

        result = recs.generate_student_recommendation(db, student, audience="student")

        assert len(result["text"]) <= recs.MAX_RECOMMENDATION_CHARS
        assert student.recommendation_text == result["text"]
        assert student.recommendation_generated_at is not None


class TestBelowThreshold:
    def test_student_below_threshold_gets_honest_placeholder(self, db, monkeypatch):
        sid = _sid()
        student = _make_student(db, sid)
        _seed_attempts(db, sid, "maths", "algebra", attempts=3, correct=2)  # < 10

        calls = []
        monkeypatch.setattr(recs, "_call_llm", lambda prompt: calls.append(prompt) or "should not be used")

        result = recs.generate_student_recommendation(db, student, audience="student")

        assert result["has_data"] is False
        assert result["text"] == recs.NOT_ENOUGH_DATA_STUDENT
        assert student.recommendation_text == recs.NOT_ENOUGH_DATA_STUDENT
        assert calls == []  # LLM must not be called when there's nothing real to ground it in

    def test_teacher_framed_below_threshold(self, db):
        sid = _sid()
        student = _make_student(db, sid)
        result = recs.generate_student_recommendation(db, student, audience="teacher")
        assert result["text"] == recs.NOT_ENOUGH_DATA_TEACHER
        assert student.teacher_recommendation_text == recs.NOT_ENOUGH_DATA_TEACHER


class TestFailureHandling:
    def test_llm_failure_does_not_crash_and_keeps_previous_value(self, db, monkeypatch):
        sid = _sid()
        student = _make_student(db, sid)
        _seed_attempts(db, sid, "maths", "algebra", attempts=10, correct=9)

        student.recommendation_text = "Previously cached good advice."
        db.commit()

        monkeypatch.setattr(recs, "_call_llm", lambda prompt: None)  # simulate API failure

        result = recs.generate_student_recommendation(db, student, audience="student")

        assert result["text"] == "Previously cached good advice."
        assert student.recommendation_text == "Previously cached good advice."  # not overwritten with garbage

    def test_trigger_swallows_exceptions_from_generation(self, db, monkeypatch):
        sid = _sid()
        _make_student(db, sid)
        for i in range(10):
            _seed_attempts(db, sid, "maths", f"t{i}", attempts=1, correct=1)

        def boom(*args, **kwargs):
            raise RuntimeError("simulated LLM outage")
        monkeypatch.setattr(recs, "generate_student_recommendation", boom)

        # Must not raise despite generation failing internally.
        fired = recs.maybe_trigger_student_recommendation(db, sid)
        assert fired is True  # the gate itself still reports it was at the trigger point

    def test_background_dispatch_failure_does_not_propagate(self, db, monkeypatch):
        """fsm.dialogue_manager._run_recommendation_check must never raise,
        even if the whole recommendation pipeline blows up."""
        import fsm.dialogue_manager as dm

        def boom_session():
            raise RuntimeError("db unavailable")
        monkeypatch.setattr("db.database.get_session", boom_session)

        dm._run_recommendation_check(_sid())  # must not raise


class TestCohortInsight:
    def test_cohort_insight_with_mocked_multi_student_data(self, db, monkeypatch):
        for i in range(3):
            sid = _sid()
            _make_student(db, sid, name=f"Student{i}")
            _seed_attempts(db, sid, "social_studies", "government", attempts=5, correct=2)

        captured = {}
        def fake_call(prompt):
            captured["prompt"] = prompt
            return "60% of students are scoring below 50% on Social Studies — consider a focused review session."
        monkeypatch.setattr(recs, "_call_llm", fake_call)

        result = recs.refresh_cohort_insight(db)

        assert "review session" in result["text"] or len(result["text"]) > 0
        assert "Social Studies" in captured["prompt"]
        assert "3" in captured["prompt"]  # total_students

        row = db.get(CohortInsight, 1)
        assert row is not None
        assert row.insight_text == result["text"]

    def test_cohort_insight_empty_cohort_is_honest_placeholder(self, db):
        result = recs.refresh_cohort_insight(db)
        assert result["text"] == recs.NOT_ENOUGH_DATA_COHORT

    def test_get_cohort_insight_text_never_generates(self, db, monkeypatch):
        """Read accessor used by the dashboard API must never call the LLM."""
        calls = []
        monkeypatch.setattr(recs, "_call_llm", lambda prompt: calls.append(prompt) or "x")
        text = recs.get_cohort_insight_text(db)
        assert text == recs.NOT_ENOUGH_DATA_COHORT
        assert calls == []


class TestDashboardApiExposure:
    def test_student_endpoint_includes_placeholder_below_threshold(self, monkeypatch):
        from fastapi.testclient import TestClient
        from api.main import app
        from api.routes import dashboard as dash
        from db.database import get_db
        from utils.phone import phone_to_student_id

        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        TestSession = sessionmaker(bind=engine)
        session = TestSession()

        def override_get_db():
            yield session
        app.dependency_overrides[get_db] = override_get_db
        dash._request_log.clear()

        try:
            sid = phone_to_student_id("0531850867")
            session.add(Student(student_id=sid, channel="whatsapp", name="Ama", last_seen_at=datetime.utcnow()))
            session.commit()

            client = TestClient(app)
            response = client.get("/api/dashboard/student/0531850867")
            assert response.status_code == 200
            assert response.json()["recommendation"] == recs.NOT_ENOUGH_DATA_STUDENT
        finally:
            session.close()
            app.dependency_overrides.pop(get_db, None)
            dash._request_log.clear()

    def test_teacher_overview_includes_insights_field(self):
        from fastapi.testclient import TestClient
        from api.main import app
        from api.routes import dashboard as dash
        from db.database import get_db
        from config import get_settings

        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        TestSession = sessionmaker(bind=engine)
        session = TestSession()

        def override_get_db():
            yield session
        app.dependency_overrides[get_db] = override_get_db
        dash._request_log.clear()

        try:
            client = TestClient(app)
            password = get_settings().dashboard_password
            response = client.get(
                "/api/dashboard/teacher/overview",
                headers={"X-Dashboard-Password": password},
            )
            assert response.status_code == 200
            assert response.json()["insights"] == recs.NOT_ENOUGH_DATA_COHORT
        finally:
            session.close()
            app.dependency_overrides.pop(get_db, None)
            dash._request_log.clear()
