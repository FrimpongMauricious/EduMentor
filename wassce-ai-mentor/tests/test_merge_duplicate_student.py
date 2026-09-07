"""
tests/test_merge_duplicate_student.py — Regression coverage for
scripts/merge_duplicate_student.py, specifically its post-merge
recommendation regeneration step.

Root cause this guards against: ai.recommendations.
maybe_trigger_student_recommendation() fires ONLY on the exact interaction
that pushes a student's live total answered-question count (summed fresh
from performance_vectors on every call — there is no stored "last
triggered at" checkpoint anywhere) across an exact multiple of
RECOMMENDATION_TRIGGER_EVERY (10). A merge changes that live total in one
step, out of band from the normal answer-by-answer flow, and the combined
total is not guaranteed to land on a multiple of 10 (119 + 6 = 125, the
real confirmed case, does not). Left unhandled, a merged student with
well over the threshold's worth of real data would keep showing whatever
was cached before the merge (or the generic "not enough data yet"
placeholder, if nothing was ever cached) until enough brand-new answers
happened to arrive at the next boundary — potentially never, if the
student doesn't come back. The merge script must force a regeneration
itself whenever the merged total already qualifies.

Every test here mocks the actual LLM call (ai.recommendations._call_llm),
matching the convention in tests/test_recommendations.py.
"""
import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import ai.recommendations as recs
import scripts.merge_duplicate_student as merge_script
from db.database import Base
from db import models  # noqa: F401 — register models with Base
from db.models import Student, PerformanceVector


def _sid() -> str:
    return uuid.uuid4().hex + uuid.uuid4().hex


@pytest.fixture
def db(monkeypatch):
    """Point both the test itself and the merge script's SessionLocal at
    the same isolated in-memory engine (StaticPool so every connection —
    including the one merge_script.merge() opens itself — shares one DB)."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    monkeypatch.setattr(merge_script, "SessionLocal", TestSession)

    session = TestSession()
    try:
        yield session
    finally:
        session.close()


def _make_student(db, sid: str, name: str, phone_number: str | None) -> Student:
    student = Student(
        student_id=sid, channel="whatsapp", name=name, phone_number=phone_number,
        registered_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc),
    )
    db.add(student)
    db.commit()
    return student


def _seed_attempts(db, sid: str, subject: str, topic: str, attempts: int, correct: int):
    db.add(PerformanceVector(
        student_id=sid, subject=subject, topic=topic,
        difficulty="easy", attempts=attempts, correct=correct,
    ))
    db.commit()


class TestMergeForcesRecommendationRegeneration:
    def test_merge_landing_off_the_10_boundary_still_regenerates(self, db, monkeypatch):
        """The exact confirmed real-world case: 119 + 6 = 125, which is NOT
        a multiple of 10 — the normal trigger would never fire for this
        total on its own. The merge must force it anyway."""
        keep_id, retire_id = _sid(), _sid()
        _make_student(db, keep_id, "Mauricious Frimpong", "whatsapp:+233241234567")
        _make_student(db, retire_id, "Mauricious Frimpong", None)
        _seed_attempts(db, keep_id, "maths", "algebra", attempts=119, correct=67)
        _seed_attempts(db, retire_id, "maths", "algebra", attempts=6, correct=4)

        monkeypatch.setattr(recs, "_call_llm", lambda prompt: json.dumps(["Focus on algebra practice."]))
        monkeypatch.setattr("builtins.input", lambda _: "MERGE")

        merge_script.merge(keep_id, retire_id, execute=True)
        db.expire_all()  # merge_script wrote via its own session; drop stale cached state

        merged = db.get(Student, keep_id)
        assert 125 % recs.RECOMMENDATION_TRIGGER_EVERY != 0  # sanity: confirms the scenario
        assert json.loads(merged.recommendation_text) == ["Focus on algebra practice."]
        assert json.loads(merged.teacher_recommendation_text) == ["Focus on algebra practice."]
        assert merged.recommendation_generated_at is not None

        total_attempts = sum(
            pv.attempts for pv in db.query(PerformanceVector).filter(
                PerformanceVector.student_id == keep_id
            ).all()
        )
        assert total_attempts == 125  # merge itself stayed lossless

    def test_merge_below_threshold_does_not_call_llm(self, db, monkeypatch):
        """A merge that doesn't reach the threshold must not generate a
        recommendation at all (there's genuinely nothing to ground it in)."""
        keep_id, retire_id = _sid(), _sid()
        _make_student(db, keep_id, "Kwame", "whatsapp:+233241234567")
        _make_student(db, retire_id, "Kwame", None)
        _seed_attempts(db, keep_id, "maths", "algebra", attempts=3, correct=2)
        _seed_attempts(db, retire_id, "maths", "algebra", attempts=2, correct=1)

        calls = []
        monkeypatch.setattr(recs, "_call_llm", lambda prompt: calls.append(prompt) or "unused")
        monkeypatch.setattr("builtins.input", lambda _: "MERGE")

        merge_script.merge(keep_id, retire_id, execute=True)
        db.expire_all()

        assert calls == []
        merged = db.get(Student, keep_id)
        assert merged.recommendation_text is None

    def test_dry_run_never_touches_recommendation_fields(self, db, monkeypatch):
        """Without --execute, nothing is written at all — including no
        recommendation regeneration."""
        keep_id, retire_id = _sid(), _sid()
        _make_student(db, keep_id, "Ama", "whatsapp:+233241234567")
        _make_student(db, retire_id, "Ama", None)
        _seed_attempts(db, keep_id, "maths", "algebra", attempts=119, correct=67)
        _seed_attempts(db, retire_id, "maths", "algebra", attempts=6, correct=4)

        calls = []
        monkeypatch.setattr(recs, "_call_llm", lambda prompt: calls.append(prompt) or "unused")

        merge_script.merge(keep_id, retire_id, execute=False)

        assert calls == []
        # Both rows must still exist untouched — dry run made no changes.
        assert db.get(Student, keep_id) is not None
        assert db.get(Student, retire_id) is not None
        assert db.get(Student, keep_id).recommendation_text is None
