"""
test_module/engine.py — Pre/Post Test administration engine.

Implements FR-31 through FR-34:
  FR-31: deliver fixed 20-question WASSCE-style test
  FR-32: test bank is separate from adaptive practice corpus
  FR-33: record responses, total score, per-subject breakdown
  FR-34: send student their score after completion
"""
import json
import uuid
import os
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import select

from db.models import TestAttempt
from fsm.answer_evaluator import evaluate_answer
from utils.logger import get_logger

logger = get_logger(__name__)

# ── Load test bank into memory once at import ───────────────────────────────
TEST_BANK_PATH = "data/corpus/wassce_test_bank.json"

def _load_test_bank() -> list[dict]:
    if not os.path.exists(TEST_BANK_PATH):
        logger.error(f"Test bank not found at {TEST_BANK_PATH}")
        return []
    with open(TEST_BANK_PATH, "r", encoding="utf-8") as f:
        bank = json.load(f)
    logger.info(f"Loaded test bank: {len(bank)} questions")
    return bank


TEST_BANK = _load_test_bank()
TOTAL_QUESTIONS = len(TEST_BANK)


# ── State helpers ───────────────────────────────────────────────────────────
def get_active_test(db: Session, student_id: str) -> Optional[TestAttempt]:
    """Return the student's in-progress test, or None."""
    stmt = select(TestAttempt).where(
        TestAttempt.student_id == student_id,
        TestAttempt.completed_at.is_(None),
    ).order_by(TestAttempt.started_at.desc())
    return db.scalars(stmt).first()


def has_completed_pretest(db: Session, student_id: str) -> bool:
    stmt = select(TestAttempt).where(
        TestAttempt.student_id == student_id,
        TestAttempt.test_type == "pre",
        TestAttempt.completed_at.is_not(None),
    )
    return db.scalars(stmt).first() is not None


def has_completed_posttest(db: Session, student_id: str) -> bool:
    stmt = select(TestAttempt).where(
        TestAttempt.student_id == student_id,
        TestAttempt.test_type == "post",
        TestAttempt.completed_at.is_not(None),
    )
    return db.scalars(stmt).first() is not None


# ── Lifecycle ───────────────────────────────────────────────────────────────
def start_test(db: Session, student_id: str) -> TestAttempt:
    """
    Start a new test attempt. Test type is auto-detected:
      - If no completed pre-test: this is the 'pre' test.
      - If pre-test done but no post-test: this is the 'post' test.
      - If both done: allow re-take as 'post'.
    """
    # Cancel any existing in-progress attempt
    active = get_active_test(db, student_id)
    if active is not None:
        logger.info(f"Cancelling existing in-progress test {active.attempt_id[:8]}...")
        db.delete(active)
        db.commit()

    if not has_completed_pretest(db, student_id):
        test_type = "pre"
    elif not has_completed_posttest(db, student_id):
        test_type = "post"
    else:
        # Allow re-take as a 'post' attempt — useful for repeat testing
        test_type = "post"

    attempt = TestAttempt(
        attempt_id=str(uuid.uuid4()),
        student_id=student_id,
        test_type=test_type,
        started_at=datetime.now(timezone.utc),
        responses=json.dumps([]),
    )
    db.add(attempt)
    db.commit()
    logger.info(f"Started {test_type}-test for student={student_id[:12]}... attempt={attempt.attempt_id[:8]}")
    return attempt


def current_question_index(attempt: TestAttempt) -> int:
    """How many questions have been ANSWERED so far (excludes show-markers)."""
    responses = json.loads(attempt.responses or "[]")
    return sum(1 for r in responses if "correct" in r)


def is_current_question_shown(attempt: TestAttempt) -> bool:
    """True if the current question has been displayed to the student but not yet answered."""
    idx = current_question_index(attempt)
    if idx >= TOTAL_QUESTIONS:
        return False
    current_test_id = TEST_BANK[idx]["test_id"]
    responses = json.loads(attempt.responses or "[]")
    return any(r.get("shown") == current_test_id for r in responses)


def mark_question_shown(db: Session, attempt: TestAttempt) -> None:
    """Record that the current question has been displayed (not yet answered)."""
    idx = current_question_index(attempt)
    if idx >= TOTAL_QUESTIONS:
        return
    current_test_id = TEST_BANK[idx]["test_id"]
    responses = json.loads(attempt.responses or "[]")
    responses.append({"shown": current_test_id})
    attempt.responses = json.dumps(responses)
    db.commit()


def get_current_question(attempt: TestAttempt) -> Optional[dict]:
    """Return the next unanswered question dict, or None if test is finished."""
    idx = current_question_index(attempt)
    if idx >= TOTAL_QUESTIONS:
        return None
    return TEST_BANK[idx]


def record_answer(db: Session, attempt: TestAttempt, student_answer: str) -> dict:
    """
    Record the student's answer to the current question.
    Returns a dict: {result, correct_answer, explanation, finished, next_question}.
    """
    current_q = get_current_question(attempt)
    if current_q is None:
        return {"result": "no_question", "finished": True, "next_question": None}

    result = evaluate_answer(student_answer, current_q["correct_answer"])
    is_correct = result in {"correct", "partial"}  # partial counted as correct for test scoring

    responses = json.loads(attempt.responses or "[]")
    responses.append({
        "test_id": current_q["test_id"],
        "subject": current_q["subject"],
        "student_answer": student_answer,
        "correct": is_correct,
        "result_label": result,
    })
    attempt.responses = json.dumps(responses)
    db.commit()

    next_q = get_current_question(attempt)
    finished = next_q is None
    if finished:
        finalise_test(db, attempt)

    return {
        "result": result,
        "is_correct": is_correct,
        "correct_answer": current_q["correct_answer"],
        "explanation": current_q["explanation"],
        "finished": finished,
        "next_question": next_q,
    }


def finalise_test(db: Session, attempt: TestAttempt) -> dict:
    """Compute total score + subject breakdown, mark complete."""
    responses = json.loads(attempt.responses or "[]")
    # Filter to actual answer entries only (exclude show-markers)
    answer_entries = [r for r in responses if "correct" in r]
    total_correct = sum(1 for r in answer_entries if r["correct"])

    subject_scores: dict[str, dict] = {}
    for r in answer_entries:
        s = subject_scores.setdefault(r["subject"], {"correct": 0, "total": 0})
        s["total"] += 1
        if r["correct"]:
            s["correct"] += 1

    attempt.completed_at = datetime.now(timezone.utc)
    attempt.total_score = total_correct
    attempt.subject_scores = json.dumps(subject_scores)
    db.commit()

    logger.info(
        f"Test finalised | student={attempt.student_id[:12]}... "
        f"type={attempt.test_type} score={total_correct}/{TOTAL_QUESTIONS}"
    )

    return {
        "test_type": attempt.test_type,
        "total_score": total_correct,
        "max_score": TOTAL_QUESTIONS,
        "subject_scores": subject_scores,
    }


def format_test_results(attempt: TestAttempt) -> str:
    """Format final results for student-facing message (channel-agnostic)."""
    subject_scores = json.loads(attempt.subject_scores or "{}")
    display_names = {
        "maths": "Maths",
        "english": "English",
        "science": "Science",
        "social_studies": "Social Studies",
    }
    label = "Pre-Test" if attempt.test_type == "pre" else "Post-Test"
    lines = [
        f"{label} Complete!",
        f"Score: {attempt.total_score}/{TOTAL_QUESTIONS}",
        "",
        "Subject breakdown:",
    ]
    for subj_key, display in display_names.items():
        s = subject_scores.get(subj_key, {"correct": 0, "total": 0})
        lines.append(f"{display}: {s['correct']}/{s['total']}")

    if attempt.test_type == "pre":
        lines.append("")
        lines.append("Now start practising! Reply MENU to begin.")
    else:
        lines.append("")
        lines.append("Well done! Reply MENU to continue practising.")

    return "\n".join(lines)
