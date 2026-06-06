"""
tests/test_adaptive.py — Adaptive engine unit tests.

Run via: pytest tests/test_adaptive.py -v
"""
import pytest
import uuid
import json
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.database import Base
from db import models  # noqa: F401
from db.models import Student, SessionRow, Interaction, PerformanceVector
from adaptive.engine import (
    update_performance,
    compute_difficulty_for_subject,
    identify_weakest_subject,
    identify_weakest_topic,
    pick_next_question,
    get_student_profile,
)


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


def _make_student(db, student_id: str = None) -> str:
    sid = student_id or (uuid.uuid4().hex + uuid.uuid4().hex)
    db.add(Student(student_id=sid, channel="whatsapp"))
    db.commit()
    return sid


def _make_session(db, student_id: str) -> SessionRow:
    s = SessionRow(
        session_id=str(uuid.uuid4()),
        student_id=student_id,
        started_at=datetime.now(timezone.utc),
        last_active_at=datetime.now(timezone.utc),
        fsm_state="QUESTION_DELIVERY",
        current_subject="maths",
        current_difficulty="easy",
        question_history=json.dumps([]),
    )
    db.add(s)
    db.commit()
    return s


class TestPerformanceUpdate:
    def test_first_attempt_creates_row(self, db):
        sid = _make_student(db)
        update_performance(db, sid, "maths", "algebra", "easy", correct=True)
        pv = db.query(PerformanceVector).filter_by(student_id=sid).first()
        assert pv is not None
        assert pv.attempts == 1
        assert pv.correct == 1
        assert pv.accuracy == 1.0

    def test_subsequent_attempts_increment(self, db):
        sid = _make_student(db)
        update_performance(db, sid, "maths", "algebra", "easy", correct=True)
        update_performance(db, sid, "maths", "algebra", "easy", correct=False)
        update_performance(db, sid, "maths", "algebra", "easy", correct=True)
        pv = db.query(PerformanceVector).filter_by(student_id=sid).first()
        assert pv.attempts == 3
        assert pv.correct == 2
        assert abs(pv.accuracy - 2 / 3) < 0.001

    def test_separate_rows_per_topic(self, db):
        sid = _make_student(db)
        update_performance(db, sid, "maths", "algebra", "easy", correct=True)
        update_performance(db, sid, "maths", "geometry", "easy", correct=True)
        rows = db.query(PerformanceVector).filter_by(student_id=sid).all()
        assert len(rows) == 2


class TestDifficultyProgression:
    def test_default_difficulty_is_easy(self, db):
        sid = _make_student(db)
        assert compute_difficulty_for_subject(db, sid, "maths") == "easy"

    def test_stays_easy_below_threshold(self, db):
        sid = _make_student(db)
        update_performance(db, sid, "maths", "algebra", "easy", correct=True)
        update_performance(db, sid, "maths", "algebra", "easy", correct=False)
        # 1/2 = 50% < 70% threshold
        assert compute_difficulty_for_subject(db, sid, "maths") == "easy"

    def test_stays_easy_below_min_attempts(self, db):
        sid = _make_student(db)
        # 2/2 = 100% but only 2 attempts (need >= 3)
        update_performance(db, sid, "maths", "algebra", "easy", correct=True)
        update_performance(db, sid, "maths", "algebra", "easy", correct=True)
        assert compute_difficulty_for_subject(db, sid, "maths") == "easy"

    def test_advances_to_medium_at_threshold(self, db):
        sid = _make_student(db)
        # 3/4 = 75% >= 70%, 4 attempts >= 3
        for _ in range(3):
            update_performance(db, sid, "maths", "algebra", "easy", correct=True)
        update_performance(db, sid, "maths", "algebra", "easy", correct=False)
        assert compute_difficulty_for_subject(db, sid, "maths") == "medium"

    def test_advances_to_hard(self, db):
        sid = _make_student(db)
        for _ in range(3):
            update_performance(db, sid, "maths", "algebra", "easy", correct=True)
        for _ in range(3):
            update_performance(db, sid, "maths", "geometry", "medium", correct=True)
        assert compute_difficulty_for_subject(db, sid, "maths") == "hard"


class TestWeakestArea:
    def test_no_data_returns_none(self, db):
        sid = _make_student(db)
        assert identify_weakest_subject(db, sid) is None

    def test_identifies_lowest_accuracy_subject(self, db):
        sid = _make_student(db)
        update_performance(db, sid, "maths", "algebra", "easy", correct=True)
        update_performance(db, sid, "maths", "algebra", "easy", correct=True)
        update_performance(db, sid, "english", "grammar", "easy", correct=False)
        update_performance(db, sid, "english", "grammar", "easy", correct=False)
        update_performance(db, sid, "science", "biology", "easy", correct=True)
        update_performance(db, sid, "science", "biology", "easy", correct=False)
        assert identify_weakest_subject(db, sid) == "english"

    def test_requires_minimum_attempts(self, db):
        sid = _make_student(db)
        # Only 1 attempt — below the minimum of 2
        update_performance(db, sid, "english", "grammar", "easy", correct=False)
        assert identify_weakest_subject(db, sid) is None

    def test_weakest_topic(self, db):
        sid = _make_student(db)
        update_performance(db, sid, "maths", "algebra", "easy", correct=True)
        update_performance(db, sid, "maths", "algebra", "easy", correct=True)
        update_performance(db, sid, "maths", "geometry", "easy", correct=False)
        update_performance(db, sid, "maths", "geometry", "easy", correct=False)
        assert identify_weakest_topic(db, sid, "maths") == "geometry"


class TestPickNextQuestion:
    def test_returns_question_in_requested_subject(self, db):
        sid = _make_student(db)
        s = _make_session(db, sid)
        q = pick_next_question(db, s, requested_subject="maths")
        assert q is not None
        assert q["subject"] == "maths"

    def test_no_repeat_correctly_answered(self, db):
        sid = _make_student(db)
        s = _make_session(db, sid)
        s.question_history = json.dumps(["MATH-001"])
        db.add(Interaction(
            interaction_id=str(uuid.uuid4()),
            session_id=s.session_id,
            student_id=sid,
            timestamp=datetime.now(timezone.utc),
            channel="whatsapp",
            fsm_state="QUESTION_DELIVERY",
            question_id="MATH-001",
            evaluation_result="correct",
        ))
        db.commit()

        for _ in range(10):
            q = pick_next_question(db, s, requested_subject="maths")
            if q is not None:
                assert q["question_id"] != "MATH-001"

    def test_returns_none_when_corpus_exhausted(self, db):
        sid = _make_student(db)
        s = _make_session(db, sid)
        for qid in ["MATH-001", "MATH-002", "MATH-003", "MATH-004", "MATH-005", "MATH-006"]:
            db.add(Interaction(
                interaction_id=str(uuid.uuid4()),
                session_id=s.session_id,
                student_id=sid,
                timestamp=datetime.now(timezone.utc),
                channel="whatsapp",
                fsm_state="QUESTION_DELIVERY",
                question_id=qid,
                evaluation_result="correct",
            ))
        db.commit()

        q = pick_next_question(db, s, requested_subject="maths")
        assert q is None


class TestStudentProfile:
    def test_empty_profile(self, db):
        sid = _make_student(db)
        profile = get_student_profile(db, sid)
        assert profile["student_id"] == sid
        assert profile["subjects"] == {}
        assert profile["weakest_subject"] is None

    def test_profile_aggregates_performance(self, db):
        sid = _make_student(db)
        update_performance(db, sid, "maths", "algebra", "easy", correct=True)
        update_performance(db, sid, "maths", "algebra", "easy", correct=False)
        update_performance(db, sid, "maths", "geometry", "medium", correct=True)

        profile = get_student_profile(db, sid)
        assert "maths" in profile["subjects"]
        assert profile["subjects"]["maths"]["attempts"] == 3
        assert profile["subjects"]["maths"]["correct"] == 2
        assert profile["subjects"]["maths"]["accuracy"] == 66.7
