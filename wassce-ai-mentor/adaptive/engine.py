"""
adaptive/engine.py — Adaptive question selection based on per-student performance.

Implements FR-26 through FR-30:
  FR-26: per-subject, per-difficulty accuracy tracking
  FR-27: weighted-random selection prioritising weakest area
  FR-28: difficulty advancement at 70% threshold
  FR-29: no repeats of correctly-answered questions in same session
  FR-30: re-present incorrect questions after 5 intervening questions
"""
import json
import random
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import select, and_

from db.models import PerformanceVector, Interaction, SessionRow
from rag.retriever import get_by_subject
from config import get_settings
from utils.logger import get_logger

logger = get_logger(__name__)
settings = get_settings()

DIFFICULTY_LADDER = ["easy", "medium", "hard"]
WEAK_AREA_WEIGHT = 0.70   # FR-27: 70% weight to weakest subject/topic
OTHER_AREA_WEIGHT = 0.30
REPEAT_INTERVAL = 5       # FR-30: incorrect questions re-shown after 5 questions


# ──────────────────────────────────────────────────────────────────────────────
# Performance vector updates (FR-26)
# ──────────────────────────────────────────────────────────────────────────────

def update_performance(
    db: Session,
    student_id: str,
    subject: str,
    topic: str,
    difficulty: str,
    correct: bool,
) -> None:
    """
    FR-26: Update or insert the per-student performance vector after each answer.
    Called from the FSM after evaluating a student's response.
    """
    stmt = select(PerformanceVector).where(
        and_(
            PerformanceVector.student_id == student_id,
            PerformanceVector.subject == subject,
            PerformanceVector.topic == topic,
            PerformanceVector.difficulty == difficulty,
        )
    )
    pv = db.scalars(stmt).first()

    if pv is None:
        pv = PerformanceVector(
            student_id=student_id,
            subject=subject,
            topic=topic,
            difficulty=difficulty,
            attempts=1,
            correct=1 if correct else 0,
        )
        db.add(pv)
    else:
        pv.attempts = (pv.attempts or 0) + 1
        if correct:
            pv.correct = (pv.correct or 0) + 1

    db.commit()
    logger.info(
        f"Performance updated | student={student_id[:12]}... "
        f"| {subject}/{topic}/{difficulty} | "
        f"correct={pv.correct}/{pv.attempts} ({round(pv.accuracy * 100)}%)"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Difficulty progression (FR-28)
# ──────────────────────────────────────────────────────────────────────────────

def compute_difficulty_for_subject(
    db: Session,
    student_id: str,
    subject: str,
) -> str:
    """
    FR-28: Determine appropriate difficulty for the student in this subject.

    Logic:
      - Walk up EASY → MEDIUM → HARD.
      - If accuracy at current level is >= 70% and >= 3 attempts, advance one level.
      - Otherwise stay at the current level.
      - Default starting level: easy.
    """
    threshold = settings.DIFFICULTY_ADVANCE_THRESHOLD

    for level in DIFFICULTY_LADDER:
        stmt = select(PerformanceVector).where(
            and_(
                PerformanceVector.student_id == student_id,
                PerformanceVector.subject == subject,
                PerformanceVector.difficulty == level,
            )
        )
        rows = db.scalars(stmt).all()
        if not rows:
            return level

        total_attempts = sum(r.attempts for r in rows)
        total_correct = sum(r.correct for r in rows)
        if total_attempts < 3:
            return level

        accuracy = total_correct / total_attempts
        if accuracy < threshold:
            return level

        idx = DIFFICULTY_LADDER.index(level)
        if idx == len(DIFFICULTY_LADDER) - 1:
            return level

    return DIFFICULTY_LADDER[-1]


# ──────────────────────────────────────────────────────────────────────────────
# Weakest area identification (FR-27)
# ──────────────────────────────────────────────────────────────────────────────

def identify_weakest_subject(
    db: Session,
    student_id: str,
) -> Optional[str]:
    """Return the subject with lowest accuracy, or None if no sufficient data."""
    stmt = select(PerformanceVector).where(PerformanceVector.student_id == student_id)
    rows = db.scalars(stmt).all()
    if not rows:
        return None

    per_subject: dict[str, dict] = {}
    for r in rows:
        s = per_subject.setdefault(r.subject, {"attempts": 0, "correct": 0})
        s["attempts"] += r.attempts
        s["correct"] += r.correct

    qualified = {s: v for s, v in per_subject.items() if v["attempts"] >= 2}
    if not qualified:
        return None

    weakest = min(qualified.items(), key=lambda kv: kv[1]["correct"] / max(kv[1]["attempts"], 1))
    return weakest[0]


def identify_weakest_topic(
    db: Session,
    student_id: str,
    subject: str,
) -> Optional[str]:
    """Return the topic within a subject with lowest accuracy."""
    stmt = select(PerformanceVector).where(
        and_(
            PerformanceVector.student_id == student_id,
            PerformanceVector.subject == subject,
        )
    )
    rows = db.scalars(stmt).all()
    if not rows:
        return None

    per_topic: dict[str, dict] = {}
    for r in rows:
        t = per_topic.setdefault(r.topic, {"attempts": 0, "correct": 0})
        t["attempts"] += r.attempts
        t["correct"] += r.correct

    qualified = {t: v for t, v in per_topic.items() if v["attempts"] >= 1}
    if not qualified:
        return None

    weakest = min(qualified.items(), key=lambda kv: kv[1]["correct"] / max(kv[1]["attempts"], 1))
    return weakest[0]


# ──────────────────────────────────────────────────────────────────────────────
# Question history helpers (FR-29, FR-30)
# ──────────────────────────────────────────────────────────────────────────────

def _get_correctly_answered_qids(db: Session, session_id: str) -> set[str]:
    """FR-29: questions answered correctly in this session — never repeat."""
    stmt = select(Interaction.question_id).where(
        and_(
            Interaction.session_id == session_id,
            Interaction.evaluation_result == "correct",
            Interaction.question_id.is_not(None),
        )
    )
    return {row for row in db.scalars(stmt).all() if row}


def _get_incorrect_qids_eligible_for_repeat(
    db: Session,
    session_id: str,
) -> set[str]:
    """
    FR-30: questions answered incorrectly that are eligible for repeat
    (i.e. at least 5 intervening questions have been answered since).
    """
    stmt = select(
        Interaction.question_id,
        Interaction.evaluation_result,
        Interaction.timestamp,
    ).where(
        and_(
            Interaction.session_id == session_id,
            Interaction.question_id.is_not(None),
            Interaction.evaluation_result.is_not(None),
        )
    ).order_by(Interaction.timestamp.asc())

    rows = db.execute(stmt).all()

    eligible = set()
    for i, (qid, result, _) in enumerate(rows):
        if result in {"incorrect", "partial"}:
            subsequent = sum(
                1 for j, (qid2, res2, _) in enumerate(rows)
                if j > i and qid2 != qid and res2 in {"correct", "incorrect", "partial", "skip"}
            )
            if subsequent >= REPEAT_INTERVAL:
                eligible.add(qid)
    return eligible


def _get_recently_delivered_qids(session_row: SessionRow) -> set[str]:
    """The last N question_ids delivered in this session (from session.question_history)."""
    history = json.loads(session_row.question_history or "[]")
    return set(history)


# ──────────────────────────────────────────────────────────────────────────────
# Main entry point: adaptive question selection
# ──────────────────────────────────────────────────────────────────────────────

def pick_next_question(
    db: Session,
    session: SessionRow,
    requested_subject: Optional[str] = None,
    question_type: Optional[str] = None,
) -> Optional[dict]:
    """
    Adaptive question selection.

    Args:
        db: Active DB session.
        session: The student's active SessionRow.
        requested_subject: If set, restrict to this subject. If None, the
            adaptive engine chooses subject based on FR-27 weighting.

    Returns:
        A dict matching the retriever's output schema, or None if no
        question of the requested subject/type exists in the corpus at
        all. If the student has exhausted every eligible question for
        this subject+type in the current session, the pool resets and a
        question is returned anyway, marked with `"pool_reset": True` so
        the caller can inform the student (see FSM `no_questions_of_type`
        vs. a "you've completed everything" notice — those are different
        situations and must not be conflated).
    """
    student_id = session.student_id

    # Step 1: target subject
    if requested_subject:
        target_subject = requested_subject
    else:
        weakest = identify_weakest_subject(db, student_id)
        if weakest and random.random() < WEAK_AREA_WEIGHT:
            target_subject = weakest
        else:
            all_subjects = ["maths", "english", "science", "social_studies"]
            target_subject = random.choice(all_subjects)

    # Step 2: target difficulty (FR-28)
    target_difficulty = compute_difficulty_for_subject(db, student_id, target_subject)

    # Step 3: candidate pool — every corpus entry for the subject.
    # Previously this used retrieve() (semantic top_k=20 search against a
    # fixed, non-representative query string). That query was identical on
    # every call for a given subject+difficulty, so Chroma always returned
    # the same 20 nearest neighbours — permanently excluding every other
    # entry from ever being selected (verified: 73 of 117 Maths entries,
    # 62%, were structurally unreachable this way) and starving the
    # exclusion logic below into a tiny pool that repeated quickly. Fetching
    # the full subject pool by exact metadata match (no similarity ranking)
    # fixes both: every entry is reachable, and there is enough headroom
    # for FR-29/FR-30 exclusion to work as designed.
    candidates = get_by_subject(target_subject)
    if not candidates:
        logger.warning(f"No candidates for subject={target_subject}")
        return None

    # Step 3b: filter by question type if requested.
    if question_type:
        from rag.grader import detect_question_type
        candidates = [c for c in candidates if detect_question_type(c["question_text"]) == question_type]
        if not candidates:
            logger.info(f"No {question_type} questions for subject={target_subject} difficulty={target_difficulty}")
            return None

    # Step 4: build exclusion set (FR-29 + FR-30)
    correctly_answered = _get_correctly_answered_qids(db, session.session_id)
    recently_delivered = _get_recently_delivered_qids(session)
    repeat_eligible = _get_incorrect_qids_eligible_for_repeat(db, session.session_id)

    # Recent but not repeat-eligible are excluded; repeat-eligible return to pool
    recent_to_exclude = recently_delivered - repeat_eligible
    excluded = correctly_answered | recent_to_exclude

    # Step 5: filter pool — prefer target difficulty
    pool = [
        c for c in candidates
        if c["difficulty"] == target_difficulty and c["question_id"] not in excluded
    ]

    if not pool:
        # Fall back: any difficulty in subject
        pool = [c for c in candidates if c["question_id"] not in excluded]

    if not pool:
        # Last resort: ignore recently-delivered but still respect correct-answer exclusion
        pool = [c for c in candidates if c["question_id"] not in correctly_answered]

    pool_reset = False
    if not pool:
        # Every eligible entry for this subject+type has been answered
        # correctly in this session — there is nothing left to exclude.
        # Reset rather than dead-ending: fall back to the full candidate
        # set (still scoped to this subject+type) so the session continues.
        logger.info(
            f"Pool exhausted for student={student_id[:12]}... subject={target_subject} "
            f"type={question_type} — resetting exclusion for this subject/type."
        )
        pool = candidates
        pool_reset = True

    if not pool:
        # Unreachable in practice (candidates was already confirmed
        # non-empty above), kept as a defensive guard against future changes.
        return None

    # Step 6: weakest-topic preference (FR-27) — skip on a freshly-reset
    # pool so the student sees a genuinely fresh spread, not just their
    # weakest topic again immediately after "completing" the subject.
    if not pool_reset:
        weakest_topic = identify_weakest_topic(db, student_id, target_subject)
        if weakest_topic and random.random() < WEAK_AREA_WEIGHT:
            topic_pool = [c for c in pool if c["topic"] == weakest_topic]
            if topic_pool:
                pool = topic_pool

    chosen = dict(random.choice(pool))
    chosen["pool_reset"] = pool_reset
    logger.info(
        f"Picked question | student={student_id[:12]}... "
        f"| subject={target_subject} difficulty={target_difficulty} "
        f"topic={chosen['topic']} qid={chosen['question_id']} pool_reset={pool_reset}"
    )
    return chosen


# ──────────────────────────────────────────────────────────────────────────────
# Diagnostics (used by dashboard in Step 9)
# ──────────────────────────────────────────────────────────────────────────────

def get_student_profile(db: Session, student_id: str) -> dict:
    """Build a summary of a student's performance profile."""
    stmt = select(PerformanceVector).where(PerformanceVector.student_id == student_id)
    rows = db.scalars(stmt).all()

    by_subject: dict[str, dict] = {}
    for r in rows:
        s = by_subject.setdefault(r.subject, {"attempts": 0, "correct": 0, "topics": {}})
        s["attempts"] += r.attempts
        s["correct"] += r.correct
        s["topics"].setdefault(r.topic, {"attempts": 0, "correct": 0})
        s["topics"][r.topic]["attempts"] += r.attempts
        s["topics"][r.topic]["correct"] += r.correct

    for s in by_subject.values():
        s["accuracy"] = round((s["correct"] / max(s["attempts"], 1)) * 100, 1)
        for t in s["topics"].values():
            t["accuracy"] = round((t["correct"] / max(t["attempts"], 1)) * 100, 1)

    return {
        "student_id": student_id,
        "subjects": by_subject,
        "weakest_subject": identify_weakest_subject(db, student_id),
    }
