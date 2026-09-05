"""
ai/recommendations.py — AI-generated learning recommendations, grounded
strictly in each student's (or the cohort's) real stored interaction data.

Recomputed periodically — every RECOMMENDATION_TRIGGER_EVERY answered
questions per student (see the trigger in
fsm/dialogue_manager._dispatch_recommendation_check) — never generated
live on a dashboard page view. The dashboard API endpoints
(api/routes/dashboard.py) only ever read the cached text this module
writes to the Student row / CohortInsight singleton.

Grounding: every prompt built here embeds ONLY numbers already computed
from PerformanceVector rows (via adaptive.engine.get_student_profile,
the same aggregation the dashboard itself uses) — no free-text student
answers, no invented facts. _GROUNDING_RULES additionally instructs the
model not to fabricate specificity beyond what's provided, and to say
something honest and generic when the data is too sparse.
"""
from datetime import datetime, timezone
from typing import Literal, Optional

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from db.models import Student, PerformanceVector, CohortInsight
from adaptive.engine import get_student_profile
from utils.logger import get_logger

logger = get_logger(__name__)

# How often (in total answered questions) a student's cached recommendation
# is regenerated. Must match the check in dialogue_manager's trigger.
RECOMMENDATION_TRIGGER_EVERY = 10

MAX_RECOMMENDATION_CHARS = 400

SUBJECT_DISPLAY = {
    "maths": "Mathematics",
    "english": "English Language",
    "science": "Integrated Science",
    "social_studies": "Social Studies",
}

NOT_ENOUGH_DATA_STUDENT = (
    "Not enough data yet to identify a clear pattern — answer a few more "
    "questions to unlock a personalised recommendation."
)
NOT_ENOUGH_DATA_TEACHER = (
    "Not enough data yet on this student to suggest a specific action — "
    "encourage them to answer a few more questions."
)
NOT_ENOUGH_DATA_COHORT = (
    "Not enough cohort data yet to identify a class-wide pattern — check "
    "back once more students have answered questions."
)

_GROUNDING_RULES = """RULES — follow these exactly:
- Use ONLY the data provided below. Do not invent, assume, or guess any fact not present in it (no topic names, scores, or events that aren't listed).
- If the data is too sparse to say something specific, say something honest and generic instead of fabricating detail.
- Keep the response to 2-4 sentences, under 400 characters total.
- Plain sentences only — no markdown, no headers, no bullet points."""

_STUDENT_FRAMING = (
    "Write DIRECTLY to the student, addressing them as 'you'. Tell them "
    "specifically what to focus on next and how, based only on the data below. "
    "This same message may also be read by a parent/guardian supporting the "
    "student, so keep it something a supportive adult could act on too."
)
_TEACHER_FRAMING = (
    "Write to this student's teacher. Recommend ONE specific action the "
    "teacher could take to help this individual student, based only on the "
    "data below."
)
_COHORT_FRAMING = (
    "Write a class-wide insight for a teacher, based on the cohort data "
    "below. Point out one pattern worth addressing across the class and "
    "suggest one concrete action (e.g. a focused review session)."
)


def _get_client():
    # Reuse the exact same OpenAI client/model configuration already used
    # for answer grading (rag/grader.py) — no new credentials, no new config.
    from rag.grader import _get_client as _shared_get_client
    return _shared_get_client()


def _model_name() -> str:
    from rag.grader import _MODEL_NAME
    return _MODEL_NAME


def _truncate(text: str, limit: int = MAX_RECOMMENDATION_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    # Reserve one character for the ellipsis so the result never exceeds `limit`.
    cut = text[:limit - 1].rsplit(" ", 1)[0] or text[:limit - 1]
    return cut.rstrip(".,;: ") + "…"


def _call_llm(prompt: str) -> Optional[str]:
    """Returns the raw model text, or None on any failure (never raises)."""
    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=_model_name(),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.warning(f"Recommendation LLM call failed: {e}")
        return None


def _build_student_context(db: Session, student: Student) -> Optional[dict]:
    """
    Gather ONLY real, stored data for this student. Returns None if there
    isn't at least RECOMMENDATION_TRIGGER_EVERY answers on record yet.
    """
    profile = get_student_profile(db, student.student_id)
    subjects = profile["subjects"]
    total_attempts = sum(s["attempts"] for s in subjects.values())
    if total_attempts < RECOMMENDATION_TRIGGER_EVERY:
        return None

    by_subject = [
        {
            "subject": SUBJECT_DISPLAY.get(subj, subj),
            "attempts": data["attempts"],
            "correct": data["correct"],
            "accuracy_pct": data["accuracy"],
            "topics": [
                {"topic": topic, "attempts": t["attempts"], "accuracy_pct": t["accuracy"]}
                for topic, t in data["topics"].items()
            ],
        }
        for subj, data in subjects.items()
    ]

    weakest = profile["weakest_subject"]
    return {
        "total_questions_answered": total_attempts,
        "by_subject": by_subject,
        "weakest_subject": SUBJECT_DISPLAY.get(weakest, weakest) if weakest else None,
    }


def generate_student_recommendation(
    db: Session,
    student: Student,
    audience: Literal["student", "teacher"] = "student",
) -> dict:
    """
    Generates (or, on LLM failure, preserves) the cached recommendation for
    one student and one audience, persisting it on the Student row.

    "student" and "guardian" share the same cached field/text by design —
    the Student and Guardian dashboards call the identical
    GET /api/dashboard/student/{phone} endpoint and render the identical
    response (see api/routes/dashboard.py), so there is exactly one
    audience-appropriate text for that surface: written to the student as
    "you", legible to a guardian reading alongside them. "teacher" gets its
    own distinct, separately-cached, action-oriented text.

    Returns {"text": str, "generated_at": iso8601 str, "has_data": bool}.
    """
    field = "recommendation_text" if audience == "student" else "teacher_recommendation_text"
    not_enough_data_text = NOT_ENOUGH_DATA_STUDENT if audience == "student" else NOT_ENOUGH_DATA_TEACHER
    framing = _STUDENT_FRAMING if audience == "student" else _TEACHER_FRAMING

    now = datetime.now(timezone.utc)
    context = _build_student_context(db, student)

    if context is None:
        setattr(student, field, not_enough_data_text)
        student.recommendation_generated_at = now
        db.commit()
        return {"text": not_enough_data_text, "generated_at": now.isoformat(), "has_data": False}

    prompt = f"{framing}\n\n{_GROUNDING_RULES}\n\nSTUDENT DATA (JSON):\n{context}\n"
    raw = _call_llm(prompt)

    if raw is None:
        # LLM failed: keep whatever was already cached rather than
        # overwriting it with nothing — the triggering interaction-recording
        # flow must not crash or lose the previous good value either way.
        existing = getattr(student, field, None) or not_enough_data_text
        return {"text": existing, "generated_at": now.isoformat(), "has_data": True}

    text = _truncate(raw)
    setattr(student, field, text)
    student.recommendation_generated_at = now
    db.commit()
    return {"text": text, "generated_at": now.isoformat(), "has_data": True}


def maybe_trigger_student_recommendation(db: Session, student_id: str) -> bool:
    """
    Called after every graded interaction (see dialogue_manager). Regenerates
    the cached student- and teacher-framed recommendations — and
    opportunistically the cohort insight — only when this student's total
    answered count is an exact multiple of RECOMMENDATION_TRIGGER_EVERY.

    Returns True if it triggered a regeneration, False if it was a no-op
    (not yet at a 10/20/30... boundary) — used by tests to confirm the
    every-10-answers gate is actually enforced, not just intended.
    """
    total = db.execute(
        select(func.sum(PerformanceVector.attempts)).where(PerformanceVector.student_id == student_id)
    ).scalar()
    total = int(total or 0)
    if total == 0 or total % RECOMMENDATION_TRIGGER_EVERY != 0:
        return False

    student = db.get(Student, student_id)
    if student is None:
        return False

    try:
        generate_student_recommendation(db, student, audience="student")
    except Exception as e:
        logger.warning(f"Student-framed recommendation failed for {student_id[:12]}...: {e}")

    try:
        generate_student_recommendation(db, student, audience="teacher")
    except Exception as e:
        logger.warning(f"Teacher-framed recommendation failed for {student_id[:12]}...: {e}")

    try:
        refresh_cohort_insight(db)
    except Exception as e:
        logger.warning(f"Cohort insight refresh failed: {e}")

    return True


def _build_cohort_context(db: Session) -> Optional[dict]:
    pvs = db.execute(select(PerformanceVector)).scalars().all()
    if not pvs:
        return None

    per_subject: dict[str, dict] = {}
    for pv in pvs:
        s = per_subject.setdefault(pv.subject, {"attempts": 0, "correct": 0})
        s["attempts"] += pv.attempts
        s["correct"] += pv.correct

    total_attempts = sum(s["attempts"] for s in per_subject.values())
    if total_attempts < RECOMMENDATION_TRIGGER_EVERY:
        return None

    student_count = db.execute(select(func.count(Student.student_id))).scalar() or 0

    by_subject = [
        {
            "subject": SUBJECT_DISPLAY.get(subj, subj),
            "attempts": data["attempts"],
            "accuracy_pct": round(data["correct"] / data["attempts"] * 100, 1) if data["attempts"] else 0.0,
        }
        for subj, data in per_subject.items()
    ]

    return {
        "total_students": student_count,
        "total_questions_answered": total_attempts,
        "by_subject": by_subject,
    }


def refresh_cohort_insight(db: Session) -> dict:
    """
    Recomputes and caches the class-wide insight shown on the teacher
    overview page (CohortInsight singleton, id=1).

    Cadence: NOT recomputed on every dashboard page view. It is refreshed
    opportunistically inside maybe_trigger_student_recommendation, i.e.
    roughly once per RECOMMENDATION_TRIGGER_EVERY answers across the whole
    cohort — reusing the same trigger already required for per-student
    recommendations rather than adding a separate cron job or scheduler.
    GET /api/dashboard/teacher/overview only ever reads the cached row.
    """
    now = datetime.now(timezone.utc)
    context = _build_cohort_context(db)

    row = db.get(CohortInsight, 1)
    if row is None:
        row = CohortInsight(id=1, insight_text=NOT_ENOUGH_DATA_COHORT, generated_at=now, based_on_total_attempts=0)
        db.add(row)

    if context is None:
        row.insight_text = NOT_ENOUGH_DATA_COHORT
        row.generated_at = now
        row.based_on_total_attempts = 0
        db.commit()
        return {"text": row.insight_text, "generated_at": now.isoformat()}

    prompt = f"{_COHORT_FRAMING}\n\n{_GROUNDING_RULES}\n\nCOHORT DATA (JSON):\n{context}\n"
    raw = _call_llm(prompt)

    if raw is None:
        # Keep the previous cached insight rather than overwriting with nothing.
        db.commit()
        return {"text": row.insight_text or NOT_ENOUGH_DATA_COHORT, "generated_at": row.generated_at.isoformat()}

    row.insight_text = _truncate(raw)
    row.generated_at = now
    row.based_on_total_attempts = context["total_questions_answered"]
    db.commit()
    return {"text": row.insight_text, "generated_at": now.isoformat()}


def get_cohort_insight_text(db: Session) -> str:
    """Read-only accessor for the dashboard API — never triggers generation."""
    row = db.get(CohortInsight, 1)
    return row.insight_text if row and row.insight_text else NOT_ENOUGH_DATA_COHORT
