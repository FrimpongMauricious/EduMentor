"""
tests/test_dashboard.py — Unit tests for the teacher dashboard query functions.
"""
import uuid
from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.database import Base
from db import models  # noqa: F401
from db.models import Student, SessionRow, Interaction, PerformanceVector, TestAttempt
from dashboard.queries import (
    cohort_overview,
    subject_topic_accuracy,
    channel_stats,
    weak_topics,
    prepost_results,
)


@pytest.fixture
def engine():
    e = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(e)
    return e


@pytest.fixture
def db(engine):
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _add_student(db, channel: str = "whatsapp", last_seen_at=None) -> str:
    sid = uuid.uuid4().hex
    kwargs = {"student_id": sid, "channel": channel}
    if last_seen_at is not None:
        kwargs["last_seen_at"] = last_seen_at
    db.add(Student(**kwargs))
    db.commit()
    return sid


def _add_session(db, student_id: str) -> str:
    sess_id = str(uuid.uuid4())
    db.add(SessionRow(
        session_id=sess_id,
        student_id=student_id,
        started_at=datetime.utcnow(),
        last_active_at=datetime.utcnow(),
        fsm_state="GREETING",
        question_history="[]",
    ))
    db.commit()
    return sess_id


def _add_interaction(db, student_id: str, session_id: str, evaluation_result: str) -> None:
    db.add(Interaction(
        interaction_id=str(uuid.uuid4()),
        session_id=session_id,
        student_id=student_id,
        timestamp=datetime.utcnow(),
        channel="whatsapp",
        fsm_state="QUESTION_DELIVERY",
        question_id="Q1",
        student_response="ans",
        evaluation_result=evaluation_result,
    ))
    db.commit()


class TestCohortOverview:
    def test_empty_db_returns_zeros(self, engine):
        m = cohort_overview(engine)
        assert m["students"] == 0
        assert m["sessions"] == 0
        assert m["questions_answered"] == 0
        assert m["accuracy_pct"] == 0.0
        assert m["active_7d"] == 0

    def test_counts_students(self, engine, db):
        _add_student(db)
        _add_student(db)
        _add_student(db, channel="ussd")
        assert cohort_overview(engine)["students"] == 3

    def test_accuracy_calculation(self, engine, db):
        sid = _add_student(db)
        sess_id = _add_session(db, sid)
        for result in ["correct", "correct", "correct", "incorrect"]:
            _add_interaction(db, sid, sess_id, result)
        m = cohort_overview(engine)
        assert m["questions_answered"] == 4
        assert m["accuracy_pct"] == 75.0

    def test_active_7d_excludes_stale(self, engine, db):
        _add_student(db)  # recent (default last_seen_at = now)
        stale_time = datetime.utcnow() - timedelta(days=10)
        _add_student(db, last_seen_at=stale_time)
        assert cohort_overview(engine)["active_7d"] == 1

    def test_skip_results_not_counted_as_answered(self, engine, db):
        sid = _add_student(db)
        sess_id = _add_session(db, sid)
        _add_interaction(db, sid, sess_id, "skip")
        m = cohort_overview(engine)
        assert m["questions_answered"] == 0


class TestSubjectTopicAccuracy:
    def test_empty_returns_empty_df(self, engine):
        df = subject_topic_accuracy(engine)
        assert df.empty

    def test_accuracy_calculated_correctly(self, engine, db):
        sid = _add_student(db)
        db.add(PerformanceVector(
            student_id=sid, subject="maths", topic="algebra",
            difficulty="easy", attempts=10, correct=8,
        ))
        db.commit()
        df = subject_topic_accuracy(engine)
        assert len(df) == 1
        assert df.iloc[0]["accuracy_pct"] == 80.0
        assert df.iloc[0]["subject_display"] == "Maths"


class TestWeakTopics:
    def test_sorted_by_accuracy_ascending(self, engine, db):
        sid = _add_student(db)
        db.add(PerformanceVector(student_id=sid, subject="maths",    topic="algebra", difficulty="easy", attempts=10, correct=9))
        db.add(PerformanceVector(student_id=sid, subject="english",  topic="grammar", difficulty="easy", attempts=10, correct=3))
        db.add(PerformanceVector(student_id=sid, subject="science",  topic="biology", difficulty="easy", attempts=10, correct=5))
        db.commit()
        df = weak_topics(engine)
        assert df.iloc[0]["topic"] == "grammar"   # 30% — weakest
        assert df.iloc[1]["topic"] == "biology"   # 50%
        assert df.iloc[2]["topic"] == "algebra"   # 90% — strongest


class TestPrePostResults:
    def test_returns_correct_improvement(self, engine, db):
        sid = _add_student(db)
        now = datetime.utcnow()
        db.add(TestAttempt(
            attempt_id=str(uuid.uuid4()), student_id=sid, test_type="pre",
            started_at=now, completed_at=now, total_score=10,
            responses="[]", subject_scores="{}",
        ))
        db.add(TestAttempt(
            attempt_id=str(uuid.uuid4()), student_id=sid, test_type="post",
            started_at=now, completed_at=now, total_score=15,
            responses="[]", subject_scores="{}",
        ))
        db.commit()
        df = prepost_results(engine)
        assert len(df) == 1
        row = df.iloc[0]
        assert row["pre_score"] == 10
        assert row["post_score"] == 15
        assert row["improvement"] == 5

    def test_no_completed_attempts_returns_empty(self, engine):
        df = prepost_results(engine)
        assert df.empty
