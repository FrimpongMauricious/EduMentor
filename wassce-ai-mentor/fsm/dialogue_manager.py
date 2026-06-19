"""
fsm/dialogue_manager.py — Finite State Machine dialogue manager.

Implements FR-10 through FR-15 (Dialogue Management).
Implements FR-FSM-01 through FR-FSM-06.

Each call to handle_message() takes a student's incoming text and current
state from the database, returns the next state and the response text.
"""
import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import select, func, case

from db.models import Student, SessionRow, Interaction
from fsm.states import FSMState, parse_subject
from fsm.answer_evaluator import evaluate_answer
from fsm import messages
from rag.grader import grade_answer
from rag.retriever import get_by_id
from adaptive.engine import pick_next_question, update_performance, identify_weakest_subject
from test_module.engine import (
    start_test, get_active_test, get_current_question,
    record_answer, format_test_results, has_completed_pretest,
    has_completed_posttest, TOTAL_QUESTIONS,
    is_current_question_shown, mark_question_shown, current_question_index,
)
from config import get_settings
from utils.logger import get_logger

logger = get_logger(__name__)
settings = get_settings()


class DialogueResult:
    """Result returned from a single FSM transition."""

    def __init__(
        self,
        response: str,
        new_state: FSMState,
        question_id: Optional[str] = None,
        evaluation_result: Optional[str] = None,
        llm_response_ms: int = 0,
        retrieval_score: float = 0.0,
        end_session: bool = False,
    ):
        self.response = response
        self.new_state = new_state
        self.question_id = question_id
        self.evaluation_result = evaluation_result
        self.llm_response_ms = llm_response_ms
        self.retrieval_score = retrieval_score
        self.end_session = end_session


# ─── DB HELPERS ──────────────────────────────────────────────────────────────

def _get_or_create_student(db: Session, student_id: str, channel: str) -> Student:
    """FR-06: register student on first contact."""
    student = db.get(Student, student_id)
    if student is None:
        student = Student(
            student_id=student_id,
            channel=channel,
            registered_at=datetime.now(timezone.utc),
            last_seen_at=datetime.now(timezone.utc),
        )
        db.add(student)
        db.commit()
        logger.info(f"Registered new student: {student_id[:12]}... channel={channel}")
    else:
        student.last_seen_at = datetime.now(timezone.utc)
        db.commit()
    return student


def _get_or_create_session(db: Session, student_id: str) -> SessionRow:
    """Get the student's active session or create a new one. FR-12: expire after 30 min."""
    timeout = timedelta(minutes=settings.SESSION_TIMEOUT_MINUTES)
    now = datetime.now(timezone.utc)

    stmt = (
        select(SessionRow)
        .where(SessionRow.student_id == student_id, SessionRow.is_expired == False)  # noqa: E712
        .order_by(SessionRow.last_active_at.desc())
    )
    active = db.scalars(stmt).first()

    if active is not None:
        last = active.last_active_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if now - last <= timeout:
            return active
        active.is_expired = True
        db.commit()
        logger.info(f"Session {active.session_id[:8]} expired for student {student_id[:12]}...")

    new_session = SessionRow(
        session_id=str(uuid.uuid4()),
        student_id=student_id,
        started_at=now,
        last_active_at=now,
        fsm_state=FSMState.GREETING.value,
        current_difficulty="easy",
        question_history=json.dumps([]),
    )
    db.add(new_session)

    student = db.get(Student, student_id)
    if student:
        student.session_count = (student.session_count or 0) + 1

    db.commit()
    logger.info(f"Created new session {new_session.session_id[:8]} for student {student_id[:12]}...")
    return new_session


def _log_interaction(
    db: Session,
    session: SessionRow,
    student_id: str,
    channel: str,
    fsm_state: FSMState,
    student_response: str,
    question_id: Optional[str],
    evaluation_result: Optional[str],
    llm_response_ms: int,
    retrieval_score: float,
) -> None:
    """FR-35: log every interaction."""
    interaction = Interaction(
        interaction_id=str(uuid.uuid4()),
        session_id=session.session_id,
        student_id=student_id,
        timestamp=datetime.now(timezone.utc),
        channel=channel,
        fsm_state=fsm_state.value,
        question_id=question_id,
        student_response=student_response,
        evaluation_result=evaluation_result,
        llm_response_ms=llm_response_ms,
        retrieval_score=retrieval_score,
    )
    db.add(interaction)
    db.commit()


def _session_stats(db: Session, session_id: str) -> tuple[int, int]:
    """Return (attempted, correct) counts for a session."""
    row = db.execute(
        select(
            func.count(Interaction.interaction_id),
            func.sum(case((Interaction.evaluation_result == "correct", 1), else_=0)),
        ).where(
            Interaction.session_id == session_id,
            Interaction.question_id.is_not(None),
        )
    ).first()
    attempted = row[0] or 0
    correct = int(row[1] or 0)
    return attempted, correct


# ─── QUESTION HELPERS ─────────────────────────────────────────────────────────

def _store_question_in_session(session: SessionRow, question: dict) -> None:
    """Append question_id to session history; keep last N per config."""
    history = json.loads(session.question_history or "[]")
    history.append(question["question_id"])
    history = history[-settings.QUESTION_HISTORY_LENGTH:]
    session.question_history = json.dumps(history)
    session.current_subject = question["subject"]


def _get_current_question_meta(session: SessionRow) -> Optional[dict]:
    """Return metadata for the most recently delivered question via direct ID lookup."""
    history = json.loads(session.question_history or "[]")
    if not history:
        return None
    return get_by_id(history[-1])


# ─── MAIN ENTRY POINT ────────────────────────────────────────────────────────

def handle_message(
    db: Session,
    student_id: str,
    channel: str,
    incoming_text: str,
) -> DialogueResult:
    """
    Process one inbound student message through the FSM.

    Returns a DialogueResult containing the response text, new state,
    and metadata for logging.
    """
    student = _get_or_create_student(db, student_id, channel)
    session = _get_or_create_session(db, student_id)

    current_state = FSMState(session.fsm_state)
    text = (incoming_text or "").strip()
    text_upper = text.upper()

    logger.info(
        f"FSM | student={student_id[:12]}... state={current_state.value} input={text!r}"
    )

    # ─── GLOBAL COMMANDS (valid in any state) ─────────────────────────────
    if text_upper in {"STOP", "QUIT", "EXIT"}:
        # Cancel any in-progress test before ending session
        active_test = get_active_test(db, student_id)
        if active_test is not None:
            db.delete(active_test)
            db.commit()
        session.is_expired = True
        session.fsm_state = FSMState.GREETING.value
        db.commit()
        _log_interaction(db, session, student_id, channel, current_state,
                         text, None, None, 0, 0.0)
        return DialogueResult(
            response=messages.farewell(),
            new_state=FSMState.GREETING,
            end_session=True,
        )

    if text_upper == "HELP":
        _log_interaction(db, session, student_id, channel, current_state,
                         text, None, None, 0, 0.0)
        return DialogueResult(
            response=messages.help_message(),
            new_state=current_state,
        )

    if text_upper == "MENU":
        session.fsm_state = FSMState.SUBJECT_SELECTION.value
        session.last_active_at = datetime.now(timezone.utc)
        db.commit()
        _log_interaction(db, session, student_id, channel, current_state,
                         text, None, None, 0, 0.0)
        return DialogueResult(
            response=messages.subject_selection_prompt(),
            new_state=FSMState.SUBJECT_SELECTION,
        )

    if text_upper == "SCORE":
        attempted, correct = _session_stats(db, session.session_id)
        _log_interaction(db, session, student_id, channel, current_state,
                         text, None, None, 0, 0.0)
        return DialogueResult(
            response=messages.session_summary(attempted, correct, None),
            new_state=current_state,
        )

    if text_upper == "STARTTEST":
        if has_completed_pretest(db, student_id) and has_completed_posttest(db, student_id):
            _log_interaction(db, session, student_id, channel, current_state,
                             text, None, None, 0, 0.0)
            return DialogueResult(
                response=messages.test_already_complete(),
                new_state=current_state,
            )

        attempt = start_test(db, student_id)
        session.fsm_state = FSMState.TEST_IN_PROGRESS.value
        session.last_active_at = datetime.now(timezone.utc)
        db.commit()
        _log_interaction(db, session, student_id, channel, current_state,
                         text, None, None, 0, 0.0)
        return DialogueResult(
            response=messages.test_intro(attempt.test_type, TOTAL_QUESTIONS),
            new_state=FSMState.TEST_IN_PROGRESS,
        )

    if text_upper == "CANCEL" and current_state == FSMState.TEST_IN_PROGRESS:
        active_test = get_active_test(db, student_id)
        if active_test is not None:
            db.delete(active_test)
            db.commit()
        session.fsm_state = FSMState.GREETING.value
        db.commit()
        _log_interaction(db, session, student_id, channel, current_state,
                         text, None, None, 0, 0.0)
        return DialogueResult(
            response=messages.test_cancelled(),
            new_state=FSMState.GREETING,
        )

    # ─── STATE HANDLERS ───────────────────────────────────────────────────

    if current_state == FSMState.GREETING:
        session.fsm_state = FSMState.SUBJECT_SELECTION.value
        session.last_active_at = datetime.now(timezone.utc)
        db.commit()
        _log_interaction(db, session, student_id, channel, current_state,
                         text, None, None, 0, 0.0)
        return DialogueResult(
            response=messages.greeting(),
            new_state=FSMState.SUBJECT_SELECTION,
        )

    if current_state == FSMState.SUBJECT_SELECTION:
        subject_key = parse_subject(text)
        if subject_key is None:
            _log_interaction(db, session, student_id, channel, current_state,
                             text, None, None, 0, 0.0)
            return DialogueResult(
                response=messages.subject_invalid(),
                new_state=current_state,
            )

        question = pick_next_question(db, session, requested_subject=subject_key)
        if question is None:
            _log_interaction(db, session, student_id, channel, current_state,
                             text, None, None, 0, 0.0)
            return DialogueResult(
                response=messages.low_confidence_fallback(),
                new_state=FSMState.SUBJECT_SELECTION,
            )

        _store_question_in_session(session, question)
        session.fsm_state = FSMState.QUESTION_DELIVERY.value
        session.last_active_at = datetime.now(timezone.utc)
        db.commit()

        response = (
            messages.subject_confirmed(subject_key)
            + "\n\n"
            + messages.question_delivery(question["question_text"])
        )
        _log_interaction(db, session, student_id, channel, current_state,
                         text, question["question_id"], None, 0, question["similarity"])
        return DialogueResult(
            response=response,
            new_state=FSMState.QUESTION_DELIVERY,
            question_id=question["question_id"],
            retrieval_score=question["similarity"],
        )

    if current_state == FSMState.QUESTION_DELIVERY:
        current_q = _get_current_question_meta(session)
        if current_q is None:
            session.fsm_state = FSMState.SUBJECT_SELECTION.value
            db.commit()
            _log_interaction(db, session, student_id, channel, current_state,
                             text, None, None, 0, 0.0)
            return DialogueResult(
                response=messages.subject_selection_prompt(),
                new_state=FSMState.SUBJECT_SELECTION,
            )

        correct_ans = current_q["correct_answer"]
        expl_text = current_q["explanation"]

        if text_upper == "SKIP":
            evaluation = "skip"
            response = (
                f"{messages.answer_skipped()}\n"
                f"{messages.explanation_block(correct_ans, expl_text)}\n\n"
                f"{messages.next_action_prompt()}"
            )
        else:
            grade = grade_answer(
                current_q["question_text"],
                text,
                correct_ans,
                current_q.get("question_type"),
            )
            score = grade["score"]
            is_correct = grade["is_correct"]
            feedback = grade.get("feedback", "")

            if score >= 60:
                evaluation = "correct"
            elif score > 0:
                evaluation = "partial"
            else:
                evaluation = "incorrect"

            if is_correct and score == 100:
                verdict = "Correct! Well done."
            elif is_correct:
                verdict = f"Good attempt! Score: {score}%\n{feedback}"
            elif score > 0:
                verdict = f"Not quite. Score: {score}%\n{feedback}"
            else:
                verdict = "Not quite. Here is the correct answer:"

            response = (
                f"{verdict}\n"
                f"Answer: {correct_ans}\n"
                f"Why: {expl_text}\n\n"
                f"{messages.next_action_prompt()}"
            )

        # FR-26: update performance vector (skip counts as incorrect for tracking purposes)
        counted_correct = evaluation in {"correct", "partial"}
        update_performance(
            db=db,
            student_id=student_id,
            subject=current_q["subject"],
            topic=current_q["topic"],
            difficulty=current_q["difficulty"],
            correct=counted_correct,
        )

        session.fsm_state = FSMState.EXPLANATION.value
        session.last_active_at = datetime.now(timezone.utc)
        db.commit()

        _log_interaction(db, session, student_id, channel, current_state,
                         text, current_q["question_id"], evaluation,
                         0, current_q.get("similarity", 0.0))
        return DialogueResult(
            response=response,
            new_state=FSMState.EXPLANATION,
            question_id=current_q["question_id"],
            evaluation_result=evaluation,
            retrieval_score=current_q.get("similarity", 0.0),
        )

    if current_state == FSMState.EXPLANATION:
        if text_upper in {"NEXT", ""}:
            if not session.current_subject:
                session.fsm_state = FSMState.SUBJECT_SELECTION.value
                db.commit()
                _log_interaction(db, session, student_id, channel, current_state,
                                 text, None, None, 0, 0.0)
                return DialogueResult(
                    response=messages.subject_selection_prompt(),
                    new_state=FSMState.SUBJECT_SELECTION,
                )

            question = pick_next_question(db, session, requested_subject=session.current_subject)
            if question is None:
                attempted, correct = _session_stats(db, session.session_id)
                weakest = identify_weakest_subject(db, student_id) or session.current_subject
                session.fsm_state = FSMState.SESSION_SUMMARY.value
                db.commit()
                _log_interaction(db, session, student_id, channel, current_state,
                                 text, None, None, 0, 0.0)
                return DialogueResult(
                    response=messages.session_summary(attempted, correct, weakest),
                    new_state=FSMState.SESSION_SUMMARY,
                )

            _store_question_in_session(session, question)
            session.fsm_state = FSMState.QUESTION_DELIVERY.value
            session.last_active_at = datetime.now(timezone.utc)
            db.commit()

            _log_interaction(db, session, student_id, channel, current_state,
                             text, question["question_id"], None, 0, question["similarity"])
            return DialogueResult(
                response=messages.question_delivery(question["question_text"]),
                new_state=FSMState.QUESTION_DELIVERY,
                question_id=question["question_id"],
                retrieval_score=question["similarity"],
            )

        # Unrecognised input in EXPLANATION — stay and re-prompt
        _log_interaction(db, session, student_id, channel, current_state,
                         text, None, None, 0, 0.0)
        return DialogueResult(
            response=messages.next_action_prompt(),
            new_state=current_state,
        )

    if current_state == FSMState.SESSION_SUMMARY:
        session.fsm_state = FSMState.SUBJECT_SELECTION.value
        session.last_active_at = datetime.now(timezone.utc)
        db.commit()
        _log_interaction(db, session, student_id, channel, current_state,
                         text, None, None, 0, 0.0)
        return DialogueResult(
            response=messages.subject_selection_prompt(),
            new_state=FSMState.SUBJECT_SELECTION,
        )

    if current_state == FSMState.TEST_IN_PROGRESS:
        attempt = get_active_test(db, student_id)

        # Defensive: no active attempt — reset to greeting
        if attempt is None:
            session.fsm_state = FSMState.GREETING.value
            db.commit()
            _log_interaction(db, session, student_id, channel, current_state,
                             text, None, None, 0, 0.0)
            return DialogueResult(
                response=messages.greeting(),
                new_state=FSMState.SUBJECT_SELECTION,
            )

        current_q = get_current_question(attempt)

        # Current question not yet shown → student sent "ready" reply, display Q
        if current_q is not None and not is_current_question_shown(attempt):
            mark_question_shown(db, attempt)
            qnum = current_question_index(attempt) + 1
            response = messages.test_question(qnum, TOTAL_QUESTIONS, current_q["subject"], current_q["question_text"])
            _log_interaction(db, session, student_id, channel, current_state,
                             text, current_q["test_id"], None, 0, 0.0)
            return DialogueResult(
                response=response,
                new_state=FSMState.TEST_IN_PROGRESS,
                question_id=current_q["test_id"],
            )

        # Text is the student's answer to the current question
        outcome = record_answer(db, attempt, text)

        if outcome["finished"]:
            db.refresh(attempt)
            session.fsm_state = FSMState.GREETING.value
            db.commit()
            response = format_test_results(attempt)
            _log_interaction(db, session, student_id, channel, current_state,
                             text, current_q["test_id"] if current_q else None,
                             outcome["result"], 0, 0.0)
            return DialogueResult(
                response=response,
                new_state=FSMState.GREETING,
                question_id=current_q["test_id"] if current_q else None,
                evaluation_result=outcome["result"],
            )

        # Deliver the next question (mark it as shown immediately)
        next_q = outcome["next_question"]
        mark_question_shown(db, attempt)
        qnum = current_question_index(attempt) + 1
        response = messages.test_question(qnum, TOTAL_QUESTIONS, next_q["subject"], next_q["question_text"])
        _log_interaction(db, session, student_id, channel, current_state,
                         text, current_q["test_id"], outcome["result"], 0, 0.0)
        return DialogueResult(
            response=response,
            new_state=FSMState.TEST_IN_PROGRESS,
            question_id=next_q["test_id"],
            evaluation_result=outcome["result"],
        )

    # Defensive fallback
    logger.warning(f"Unhandled state {current_state} — resetting to GREETING")
    session.fsm_state = FSMState.GREETING.value
    db.commit()
    return DialogueResult(
        response=messages.fallback_unknown(),
        new_state=FSMState.GREETING,
    )
