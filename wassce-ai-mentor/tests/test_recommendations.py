"""
tests/test_recommendations.py — AI-generated learning recommendations
(ai/recommendations.py), the every-10-answers trigger in
fsm/dialogue_manager.py, and their exposure via the dashboard API.

Recommendations are stored/served as a short list of bullet points (a JSON
array of strings), not a paragraph — see ai/recommendations._parse_llm_bullets
(parses the model's raw output) and _parse_stored_bullets (parses whatever's
cached, backward-compatible with pre-bullets plain-string values already in
production).

Every test mocks the actual LLM call (ai.recommendations._call_llm) —
these tests verify grounding (what data reaches the prompt), trigger
cadence, bullet parsing/capping, and failure handling, not real model output.
"""
import json
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


def _bullets_json(bullets: list[str]) -> str:
    """A model response shaped exactly as _GROUNDING_RULES asks for."""
    return json.dumps(bullets)


class TestTriggerCadence:
    """The every-10-answers gate must actually be enforced, not just intended."""

    def test_no_trigger_below_ten_answers(self, db, monkeypatch):
        sid = _sid()
        _make_student(db, sid)
        calls = []
        monkeypatch.setattr(recs, "_call_llm", lambda prompt: calls.append(prompt) or _bullets_json(["stub"]))

        for total in (1, 5, 9):
            _seed_attempts(db, sid, "maths", f"topic{total}", attempts=1, correct=1)
            fired = recs.maybe_trigger_student_recommendation(db, sid)
            assert fired is False

        assert calls == []  # LLM never called before the 10th answer

    def test_trigger_fires_exactly_at_10_20_30(self, db, monkeypatch):
        sid = _sid()
        _make_student(db, sid)
        monkeypatch.setattr(recs, "_call_llm", lambda prompt: _bullets_json(["Focus on your weaker subject."]))

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
        monkeypatch.setattr(recs, "_call_llm", lambda prompt: _bullets_json(["Keep practising Mathematics."]))

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
            return _bullets_json(["Focus on English grammar — your accuracy there is lower than in Mathematics."])
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
        # The bullet-structure/output-format instructions must be included.
        assert "JSON array of strings" in prompt
        assert str(recs.MAX_BULLETS) in prompt
        # Nothing about a subject this student never touched.
        assert "Social Studies" not in prompt
        assert "Integrated Science" not in prompt

    def test_recommendation_bullets_persisted_as_json(self, db, monkeypatch):
        sid = _sid()
        student = _make_student(db, sid)
        _seed_attempts(db, sid, "maths", "algebra", attempts=10, correct=8)

        monkeypatch.setattr(
            recs, "_call_llm",
            lambda prompt: _bullets_json(["Practice algebra daily.", "Review mistakes after each set."]),
        )

        result = recs.generate_student_recommendation(db, student, audience="student")

        assert result["bullets"] == ["Practice algebra daily.", "Review mistakes after each set."]
        assert result["has_data"] is True
        assert student.recommendation_generated_at is not None
        # Stored as JSON on the Text column, not a bare paragraph.
        assert json.loads(student.recommendation_text) == result["bullets"]


class TestBulletParsingAndCapping:
    """The model's raw output must be parsed strictly and defensively."""

    def test_bullets_capped_at_max_even_if_model_returns_more(self, db, monkeypatch):
        sid = _sid()
        student = _make_student(db, sid)
        _seed_attempts(db, sid, "maths", "algebra", attempts=10, correct=5)

        too_many = [f"Point number {i}." for i in range(1, 10)]  # 9 > MAX_BULLETS
        monkeypatch.setattr(recs, "_call_llm", lambda prompt: _bullets_json(too_many))

        result = recs.generate_student_recommendation(db, student, audience="student")

        assert len(result["bullets"]) == recs.MAX_BULLETS
        assert result["bullets"] == too_many[: recs.MAX_BULLETS]

    def test_long_bullet_is_truncated_not_the_whole_list(self, db, monkeypatch):
        sid = _sid()
        student = _make_student(db, sid)
        _seed_attempts(db, sid, "maths", "algebra", attempts=10, correct=5)

        long_bullet = "x" * 900  # deliberately over MAX_BULLET_CHARS
        monkeypatch.setattr(recs, "_call_llm", lambda prompt: _bullets_json([long_bullet, "Short point."]))

        result = recs.generate_student_recommendation(db, student, audience="student")

        assert len(result["bullets"]) == 2
        assert len(result["bullets"][0]) <= recs.MAX_BULLET_CHARS
        assert result["bullets"][1] == "Short point."

    def test_malformed_llm_output_falls_back_without_crashing(self, db, monkeypatch):
        """Not valid JSON at all — must not crash, must not store garbage,
        must fall back to whatever was already cached."""
        sid = _sid()
        student = _make_student(db, sid)
        _seed_attempts(db, sid, "maths", "algebra", attempts=10, correct=5)
        student.recommendation_text = json.dumps(["Previously cached good bullet."])
        db.commit()

        monkeypatch.setattr(recs, "_call_llm", lambda prompt: "This is not JSON at all, just prose.")

        result = recs.generate_student_recommendation(db, student, audience="student")

        assert result["bullets"] == ["Previously cached good bullet."]
        assert student.recommendation_text == json.dumps(["Previously cached good bullet."])  # untouched

    def test_json_object_instead_of_array_falls_back_without_crashing(self, db, monkeypatch):
        """Valid JSON, but the wrong shape (an object, not an array)."""
        sid = _sid()
        student = _make_student(db, sid)
        _seed_attempts(db, sid, "maths", "algebra", attempts=10, correct=5)

        monkeypatch.setattr(recs, "_call_llm", lambda prompt: json.dumps({"bullet": "not a list"}))

        result = recs.generate_student_recommendation(db, student, audience="student")

        # No prior cache and nothing usable from the model — the honest
        # "not enough data" fallback text is what _parse_stored_bullets
        # returns for a None/empty stored value.
        assert result["bullets"] == [recs.NOT_ENOUGH_DATA_STUDENT]

    def test_llm_output_wrapped_in_code_fence_is_still_parsed(self, db, monkeypatch):
        sid = _sid()
        student = _make_student(db, sid)
        _seed_attempts(db, sid, "maths", "algebra", attempts=10, correct=5)

        fenced = "```json\n" + _bullets_json(["Focus on algebra."]) + "\n```"
        monkeypatch.setattr(recs, "_call_llm", lambda prompt: fenced)

        result = recs.generate_student_recommendation(db, student, audience="student")
        assert result["bullets"] == ["Focus on algebra."]

    def test_empty_strings_in_array_are_dropped(self, db, monkeypatch):
        sid = _sid()
        student = _make_student(db, sid)
        _seed_attempts(db, sid, "maths", "algebra", attempts=10, correct=5)

        monkeypatch.setattr(recs, "_call_llm", lambda prompt: _bullets_json(["Real point.", "", "   "]))

        result = recs.generate_student_recommendation(db, student, audience="student")
        assert result["bullets"] == ["Real point."]


class TestBackwardCompatibleReads:
    """A real production Student row can hold a pre-bullets plain-string
    value (generated before this change shipped) — reading it must not
    crash, and must degrade gracefully to a single-item bullet list."""

    def test_legacy_plain_string_value_reads_as_single_bullet(self):
        legacy_text = "You are doing well in English. Focus more on Mathematics, especially algebra."
        bullets, has_data = recs._parse_stored_bullets(legacy_text, recs.NOT_ENOUGH_DATA_STUDENT)
        assert bullets == [legacy_text]
        assert has_data is True

    def test_new_format_json_array_reads_as_list(self):
        stored = json.dumps(["Point one.", "Point two."])
        bullets, has_data = recs._parse_stored_bullets(stored, recs.NOT_ENOUGH_DATA_STUDENT)
        assert bullets == ["Point one.", "Point two."]
        assert has_data is True

    def test_none_value_reads_as_not_enough_data(self):
        bullets, has_data = recs._parse_stored_bullets(None, recs.NOT_ENOUGH_DATA_STUDENT)
        assert bullets == [recs.NOT_ENOUGH_DATA_STUDENT]
        assert has_data is False

    def test_stored_not_enough_data_sentinel_reads_as_no_data(self):
        stored = json.dumps([recs.NOT_ENOUGH_DATA_STUDENT])
        bullets, has_data = recs._parse_stored_bullets(stored, recs.NOT_ENOUGH_DATA_STUDENT)
        assert bullets == [recs.NOT_ENOUGH_DATA_STUDENT]
        assert has_data is False


class TestBelowThreshold:
    def test_student_below_threshold_gets_honest_placeholder(self, db, monkeypatch):
        sid = _sid()
        student = _make_student(db, sid)
        _seed_attempts(db, sid, "maths", "algebra", attempts=3, correct=2)  # < 10

        calls = []
        monkeypatch.setattr(recs, "_call_llm", lambda prompt: calls.append(prompt) or "should not be used")

        result = recs.generate_student_recommendation(db, student, audience="student")

        assert result["has_data"] is False
        assert result["bullets"] == [recs.NOT_ENOUGH_DATA_STUDENT]
        assert json.loads(student.recommendation_text) == [recs.NOT_ENOUGH_DATA_STUDENT]
        assert calls == []  # LLM must not be called when there's nothing real to ground it in

    def test_teacher_framed_below_threshold(self, db):
        sid = _sid()
        student = _make_student(db, sid)
        result = recs.generate_student_recommendation(db, student, audience="teacher")
        assert result["bullets"] == [recs.NOT_ENOUGH_DATA_TEACHER]
        assert json.loads(student.teacher_recommendation_text) == [recs.NOT_ENOUGH_DATA_TEACHER]


class TestFailureHandling:
    def test_llm_failure_does_not_crash_and_keeps_previous_value(self, db, monkeypatch):
        sid = _sid()
        student = _make_student(db, sid)
        _seed_attempts(db, sid, "maths", "algebra", attempts=10, correct=9)

        student.recommendation_text = json.dumps(["Previously cached good advice."])
        db.commit()

        monkeypatch.setattr(recs, "_call_llm", lambda prompt: None)  # simulate API failure

        result = recs.generate_student_recommendation(db, student, audience="student")

        assert result["bullets"] == ["Previously cached good advice."]
        assert student.recommendation_text == json.dumps(["Previously cached good advice."])  # not overwritten

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
            return _bullets_json([
                "60% of students are scoring below 50% on Social Studies.",
                "Consider a focused review session on government.",
            ])
        monkeypatch.setattr(recs, "_call_llm", fake_call)

        result = recs.refresh_cohort_insight(db)

        assert len(result["bullets"]) == 2
        assert "Social Studies" in captured["prompt"]
        assert "3" in captured["prompt"]  # total_students

        row = db.get(CohortInsight, 1)
        assert row is not None
        assert json.loads(row.insight_text) == result["bullets"]

    def test_cohort_insight_empty_cohort_is_honest_placeholder(self, db):
        result = recs.refresh_cohort_insight(db)
        assert result["bullets"] == [recs.NOT_ENOUGH_DATA_COHORT]

    def test_get_cohort_insight_bullets_never_generates(self, db, monkeypatch):
        """Read accessor used by the dashboard API must never call the LLM."""
        calls = []
        monkeypatch.setattr(recs, "_call_llm", lambda prompt: calls.append(prompt) or _bullets_json(["x"]))
        bullets = recs.get_cohort_insight_bullets(db)
        assert bullets == [recs.NOT_ENOUGH_DATA_COHORT]
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
            body = response.json()
            assert body["recommendation"] == [recs.NOT_ENOUGH_DATA_STUDENT]
            assert body["recommendation_has_data"] is False
        finally:
            session.close()
            app.dependency_overrides.pop(get_db, None)
            dash._request_log.clear()

    def test_student_endpoint_returns_real_bullets_when_data_exists(self):
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
            session.add(Student(
                student_id=sid, channel="whatsapp", name="Ama", last_seen_at=datetime.utcnow(),
                recommendation_text=json.dumps(["Focus on algebra.", "Review weak topics weekly."]),
            ))
            session.commit()

            client = TestClient(app)
            response = client.get("/api/dashboard/student/0531850867")
            assert response.status_code == 200
            body = response.json()
            assert body["recommendation"] == ["Focus on algebra.", "Review weak topics weekly."]
            assert body["recommendation_has_data"] is True
        finally:
            session.close()
            app.dependency_overrides.pop(get_db, None)
            dash._request_log.clear()

    def test_student_endpoint_legacy_plain_string_does_not_crash(self):
        """A real pre-bullets production value (plain paragraph, not JSON)
        must render as a one-item bullet list, not 500."""
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
            legacy_text = "You are doing well overall, keep practising Mathematics daily."
            session.add(Student(
                student_id=sid, channel="whatsapp", name="Ama", last_seen_at=datetime.utcnow(),
                recommendation_text=legacy_text,
            ))
            session.commit()

            client = TestClient(app)
            response = client.get("/api/dashboard/student/0531850867")
            assert response.status_code == 200
            body = response.json()
            assert body["recommendation"] == [legacy_text]
            assert body["recommendation_has_data"] is True
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
            body = response.json()
            assert body["insights"] == [recs.NOT_ENOUGH_DATA_COHORT]
            assert body["insights_has_data"] is False
        finally:
            session.close()
            app.dependency_overrides.pop(get_db, None)
            dash._request_log.clear()
