"""
tests/test_fsm.py — FSM dialogue manager and answer evaluator tests.

Run via: pytest tests/test_fsm.py -v
"""
import pytest
import uuid
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from db.database import Base
from db import models  # noqa: F401 — register models with Base
from fsm.dialogue_manager import handle_message
from fsm.states import FSMState
from fsm.answer_evaluator import evaluate_answer


# ─── In-memory test DB fixture ─────────────────────────────────────────────
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


def _random_student_id() -> str:
    """64-char hex string mimicking SHA-256."""
    return uuid.uuid4().hex + uuid.uuid4().hex


# ─── ANSWER EVALUATOR ───────────────────────────────────────────────────────
class TestAnswerEvaluator:
    def test_exact_match_is_correct(self):
        assert evaluate_answer("x = 5", "x = 5") == "correct"

    def test_case_insensitive_match(self):
        assert evaluate_answer("ACCRA", "Accra") == "correct"

    def test_substring_match_is_correct(self):
        assert evaluate_answer("5", "x = 5") == "correct"

    def test_fuzzy_match_is_correct(self):
        assert evaluate_answer("mitochondira", "Mitochondria") in {"correct", "partial"}

    def test_completely_wrong_is_incorrect(self):
        assert evaluate_answer("banana", "x = 5") == "incorrect"

    def test_skip_keyword(self):
        assert evaluate_answer("skip", "anything") == "skip"

    def test_empty_is_skip(self):
        assert evaluate_answer("", "anything") == "skip"

    def test_partial_token_overlap(self):
        result = evaluate_answer("equal force", "For every action there is an equal and opposite reaction")
        assert result in {"partial", "correct"}


# ─── FSM TRANSITIONS ───────────────────────────────────────────────────────
class TestFSMFlow:
    def test_first_contact_returns_greeting(self, db):
        sid = _random_student_id()
        result = handle_message(db, sid, "whatsapp", "Hi")
        assert result.new_state == FSMState.SUBJECT_SELECTION
        assert "Welcome" in result.response
        assert "Mathematics" in result.response

    def test_invalid_subject_selection_stays(self, db):
        sid = _random_student_id()
        handle_message(db, sid, "whatsapp", "Hi")
        result = handle_message(db, sid, "whatsapp", "blahblah")
        assert result.new_state == FSMState.SUBJECT_SELECTION
        assert "did not understand" in result.response.lower() or "1, 2, 3" in result.response

    def test_valid_subject_selection_delivers_question(self, db):
        sid = _random_student_id()
        handle_message(db, sid, "whatsapp", "Hi")
        result = handle_message(db, sid, "whatsapp", "1")  # Maths
        assert result.new_state == FSMState.QUESTION_DELIVERY
        assert result.question_id is not None
        assert result.question_id.startswith("MATH-")
        assert "Question" in result.response

    def test_subject_by_name(self, db):
        sid = _random_student_id()
        handle_message(db, sid, "whatsapp", "Hi")
        result = handle_message(db, sid, "whatsapp", "science")
        assert result.new_state == FSMState.QUESTION_DELIVERY
        assert result.question_id.startswith("SCI-")

    def test_answer_evaluation_to_explanation(self, db):
        sid = _random_student_id()
        handle_message(db, sid, "whatsapp", "Hi")
        handle_message(db, sid, "whatsapp", "1")  # get a maths question
        result = handle_message(db, sid, "whatsapp", "some answer")
        assert result.new_state == FSMState.EXPLANATION
        assert result.evaluation_result in {"correct", "partial", "incorrect", "skip"}
        assert "Answer:" in result.response

    def test_next_after_explanation_delivers_new_question(self, db):
        sid = _random_student_id()
        handle_message(db, sid, "whatsapp", "Hi")
        handle_message(db, sid, "whatsapp", "1")
        handle_message(db, sid, "whatsapp", "x = 5")  # answer → EXPLANATION
        result = handle_message(db, sid, "whatsapp", "NEXT")
        assert result.new_state == FSMState.QUESTION_DELIVERY
        assert result.question_id is not None

    def test_skip_advances_to_explanation(self, db):
        sid = _random_student_id()
        handle_message(db, sid, "whatsapp", "Hi")
        handle_message(db, sid, "whatsapp", "1")
        result = handle_message(db, sid, "whatsapp", "SKIP")
        assert result.new_state == FSMState.EXPLANATION
        assert result.evaluation_result == "skip"

    def test_menu_from_any_state(self, db):
        sid = _random_student_id()
        handle_message(db, sid, "whatsapp", "Hi")
        handle_message(db, sid, "whatsapp", "1")  # in QUESTION_DELIVERY
        result = handle_message(db, sid, "whatsapp", "MENU")
        assert result.new_state == FSMState.SUBJECT_SELECTION

    def test_stop_ends_session(self, db):
        sid = _random_student_id()
        handle_message(db, sid, "whatsapp", "Hi")
        handle_message(db, sid, "whatsapp", "1")
        result = handle_message(db, sid, "whatsapp", "STOP")
        assert result.end_session is True
        assert "Goodbye" in result.response or "Good luck" in result.response

    def test_help_command(self, db):
        sid = _random_student_id()
        handle_message(db, sid, "whatsapp", "Hi")
        result = handle_message(db, sid, "whatsapp", "HELP")
        assert "NEXT" in result.response
        assert "MENU" in result.response

    def test_interaction_is_logged(self, db):
        from db.models import Interaction
        sid = _random_student_id()
        handle_message(db, sid, "whatsapp", "Hi")
        handle_message(db, sid, "whatsapp", "1")
        count = db.query(Interaction).filter(Interaction.student_id == sid).count()
        assert count >= 2

    def test_student_is_persisted(self, db):
        from db.models import Student
        sid = _random_student_id()
        handle_message(db, sid, "whatsapp", "Hi")
        student = db.get(Student, sid)
        assert student is not None
        assert student.channel == "whatsapp"

    def test_question_not_repeated_in_session(self, db):
        """FR-29: questions answered in this session should not repeat."""
        sid = _random_student_id()
        handle_message(db, sid, "whatsapp", "Hi")
        handle_message(db, sid, "whatsapp", "1")  # Q1

        from db.models import SessionRow
        import json
        s = db.query(SessionRow).filter(SessionRow.student_id == sid).first()
        first_qid = json.loads(s.question_history)[-1]

        handle_message(db, sid, "whatsapp", "x = 5")  # answer
        result = handle_message(db, sid, "whatsapp", "NEXT")  # Q2

        if result.new_state == FSMState.QUESTION_DELIVERY:
            s = db.query(SessionRow).filter(SessionRow.student_id == sid).first()
            history = json.loads(s.question_history)
            assert len(history) >= 2
            assert history[-1] != first_qid
