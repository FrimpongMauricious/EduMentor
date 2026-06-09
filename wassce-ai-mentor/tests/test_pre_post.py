"""
tests/test_pre_post.py — Pre/Post test module unit tests.
"""
import pytest
import uuid
import json
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.database import Base
from db import models  # noqa: F401
from db.models import Student, TestAttempt
from test_module.engine import (
    start_test, get_active_test, get_current_question, record_answer,
    finalise_test, format_test_results, has_completed_pretest,
    has_completed_posttest, TEST_BANK, TOTAL_QUESTIONS, current_question_index,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    s = TestSession()
    try:
        yield s
    finally:
        s.close()


def _make_student(db):
    sid = uuid.uuid4().hex + uuid.uuid4().hex
    db.add(Student(student_id=sid, channel="whatsapp"))
    db.commit()
    return sid


class TestBank:
    def test_bank_has_20_questions(self):
        assert TOTAL_QUESTIONS == 20
        assert len(TEST_BANK) == 20

    def test_bank_has_5_per_subject(self):
        from collections import Counter
        counts = Counter(q["subject"] for q in TEST_BANK)
        assert counts["maths"] == 5
        assert counts["english"] == 5
        assert counts["science"] == 5
        assert counts["social_studies"] == 5

    def test_all_questions_have_required_fields(self):
        for q in TEST_BANK:
            assert "test_id" in q
            assert "subject" in q
            assert "question_text" in q
            assert "correct_answer" in q
            assert "explanation" in q

    def test_test_ids_are_unique(self):
        ids = [q["test_id"] for q in TEST_BANK]
        assert len(ids) == len(set(ids))


class TestStartTest:
    def test_first_test_is_pre(self, db):
        sid = _make_student(db)
        a = start_test(db, sid)
        assert a.test_type == "pre"
        assert a.completed_at is None
        assert json.loads(a.responses) == []

    def test_second_test_is_post(self, db):
        sid = _make_student(db)
        a = start_test(db, sid)
        # Simulate finishing the pre-test
        for q in TEST_BANK:
            record_answer(db, a, "any answer")
        # Now start a second test
        b = start_test(db, sid)
        assert b.test_type == "post"

    def test_active_test_is_replaced(self, db):
        sid = _make_student(db)
        a = start_test(db, sid)
        first_id = a.attempt_id
        b = start_test(db, sid)
        # The first attempt should be deleted
        assert b.attempt_id != first_id
        existing = db.query(TestAttempt).filter_by(attempt_id=first_id).first()
        assert existing is None


class TestRecordAnswer:
    def test_correct_answer_marked_correct(self, db):
        sid = _make_student(db)
        a = start_test(db, sid)
        first_q = TEST_BANK[0]
        outcome = record_answer(db, a, first_q["correct_answer"])
        assert outcome["is_correct"] is True

    def test_wrong_answer_marked_incorrect(self, db):
        sid = _make_student(db)
        a = start_test(db, sid)
        outcome = record_answer(db, a, "completely wrong")
        assert outcome["is_correct"] is False

    def test_responses_accumulate(self, db):
        sid = _make_student(db)
        a = start_test(db, sid)
        record_answer(db, a, "a")
        record_answer(db, a, "b")
        record_answer(db, a, "c")
        assert current_question_index(a) == 3

    def test_progress_advances(self, db):
        sid = _make_student(db)
        a = start_test(db, sid)
        assert get_current_question(a)["test_id"] == TEST_BANK[0]["test_id"]
        record_answer(db, a, "anything")
        assert get_current_question(a)["test_id"] == TEST_BANK[1]["test_id"]


class TestFinalisation:
    def test_completes_after_20_answers(self, db):
        sid = _make_student(db)
        a = start_test(db, sid)
        for i, q in enumerate(TEST_BANK):
            outcome = record_answer(db, a, q["correct_answer"])
            if i < 19:
                assert outcome["finished"] is False
            else:
                assert outcome["finished"] is True
        db.refresh(a)
        assert a.completed_at is not None
        assert a.total_score == 20  # All correct

    def test_subject_scores_recorded(self, db):
        sid = _make_student(db)
        a = start_test(db, sid)
        for q in TEST_BANK:
            record_answer(db, a, q["correct_answer"])
        db.refresh(a)
        scores = json.loads(a.subject_scores)
        assert scores["maths"]["correct"] == 5
        assert scores["english"]["correct"] == 5
        assert scores["science"]["correct"] == 5
        assert scores["social_studies"]["correct"] == 5

    def test_partial_score(self, db):
        sid = _make_student(db)
        a = start_test(db, sid)
        # Answer first 10 correctly, last 10 wrong
        for i, q in enumerate(TEST_BANK):
            answer = q["correct_answer"] if i < 10 else "wrong"
            record_answer(db, a, answer)
        db.refresh(a)
        assert a.total_score == 10


class TestCompletionFlags:
    def test_pretest_flag(self, db):
        sid = _make_student(db)
        assert has_completed_pretest(db, sid) is False
        a = start_test(db, sid)
        for q in TEST_BANK:
            record_answer(db, a, q["correct_answer"])
        assert has_completed_pretest(db, sid) is True

    def test_posttest_flag(self, db):
        sid = _make_student(db)
        # Complete pre-test
        a = start_test(db, sid)
        for q in TEST_BANK:
            record_answer(db, a, q["correct_answer"])
        assert has_completed_posttest(db, sid) is False
        # Complete post-test
        b = start_test(db, sid)
        for q in TEST_BANK:
            record_answer(db, b, q["correct_answer"])
        assert has_completed_posttest(db, sid) is True


class TestResultFormatting:
    def test_format_includes_score(self, db):
        sid = _make_student(db)
        a = start_test(db, sid)
        for q in TEST_BANK:
            record_answer(db, a, q["correct_answer"])
        db.refresh(a)
        msg = format_test_results(a)
        assert "20/20" in msg
        assert "Pre-Test" in msg
        assert "Maths" in msg
        assert "5/5" in msg
