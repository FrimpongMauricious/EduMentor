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
import json
import re
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

# Recommendations are stored and served as a short list of bullet points
# rather than a paragraph (see _parse_llm_bullets / _parse_stored_bullets).
MAX_BULLETS = 6
MAX_BULLET_CHARS = 150

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

_GROUNDING_RULES = f"""RULES — follow these exactly:
- Use ONLY the data provided below. Do not invent, assume, or guess any fact not present in it (no topic names, scores, or events that aren't listed).
- If the data is too sparse to say something specific, say something honest and generic instead of fabricating detail.
- Output a SHORT LIST OF BULLET POINTS, not a paragraph. Each bullet must be one clear, concise, actionable point — not a run-on sentence, under {MAX_BULLET_CHARS} characters.
- Use as many bullets as genuinely useful and grounded in the real data (e.g. more than one weak subject/topic each deserve their own bullet) — do not pad with filler points just to hit a number.
- Never use more than {MAX_BULLETS} bullets. If there's more that could be said, keep only the {MAX_BULLETS} most impactful points.
- Return ONLY a JSON array of strings, one string per bullet point — no markdown, no numbering, no headers, no prose outside the array. Example: ["Bullet one.", "Bullet two."]"""

_STUDENT_FRAMING = (
    "Write DIRECTLY to the student, addressing them as 'you'. Each bullet "
    "should tell them specifically what to focus on next and how, based "
    "only on the data below. This same list may also be read by a parent/"
    "guardian supporting the student, so keep it something a supportive "
    "adult could act on too."
)
_TEACHER_FRAMING = (
    "Write to this student's teacher. Each bullet should be one specific "
    "action the teacher could take to help this individual student, based "
    "only on the data below."
)
_COHORT_FRAMING = (
    "Write a class-wide insight for a teacher, based on the cohort data "
    "below. Each bullet should point out one pattern worth addressing "
    "across the class or one concrete action (e.g. a focused review "
    "session) — grounded only in the data below."
)


def _get_client():
    # Reuse the exact same OpenAI client/model configuration already used
    # for answer grading (rag/grader.py) — no new credentials, no new config.
    from rag.grader import _get_client as _shared_get_client
    return _shared_get_client()


def _model_name() -> str:
    from rag.grader import _MODEL_NAME
    return _MODEL_NAME


def _truncate(text: str, limit: int = MAX_BULLET_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    # Reserve one character for the ellipsis so the result never exceeds `limit`.
    cut = text[:limit - 1].rsplit(" ", 1)[0] or text[:limit - 1]
    return cut.rstrip(".,;: ") + "…"


def _parse_llm_bullets(raw: str) -> Optional[list[str]]:
    """
    Parse the model's raw response into a validated, capped list of bullet
    strings. Expected shape: a JSON array of strings, optionally wrapped in
    a ```json code fence — the same shape/parsing pattern already used for
    grading responses in rag/grader.py::grade_open() (strip fences,
    json.loads, validate).

    Returns None on any malformed/unparseable output; the caller falls back
    to whatever was already cached rather than showing broken data.
    """
    if not raw:
        return None
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    try:
        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, list):
        return None
    bullets = [_truncate(item) for item in parsed if isinstance(item, str) and item.strip()]
    if not bullets:
        return None
    return bullets[:MAX_BULLETS]


def _parse_stored_bullets(raw: Optional[str], not_enough_data_text: str) -> tuple[list[str], bool]:
    """
    Parse a Student.recommendation_text / teacher_recommendation_text /
    CohortInsight.insight_text column value into (bullets, has_data).

    Backward compatible with pre-bullets plain-string values — including
    real production rows generated before this change shipped: any stored
    value that isn't a JSON array of strings (a plain paragraph, most
    likely) is wrapped as a single-item list instead of raising, so old
    data renders as one bullet rather than crashing the read path.
    """
    if not raw:
        return [not_enough_data_text], False
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return [raw], True  # legacy plain-string value — treat as one real bullet
    if isinstance(parsed, list) and parsed and all(isinstance(b, str) and b.strip() for b in parsed):
        bullets = [b.strip() for b in parsed]
        return bullets, bullets != [not_enough_data_text]
    # Valid JSON but not the shape we expect — fall back to the raw stored
    # text as a single legacy bullet rather than losing it.
    return [raw], True


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

    "student" and "guardian" share the same cached field/bullets by design —
    the Student and Guardian dashboards call the identical
    GET /api/dashboard/student/{phone} endpoint and render the identical
    response (see api/routes/dashboard.py), so there is exactly one
    audience-appropriate list for that surface: written to the student as
    "you", legible to a guardian reading alongside them. "teacher" gets its
    own distinct, separately-cached, action-oriented list.

    Stored on the Student row as a JSON-encoded array of bullet strings
    (the column stays Text — see _parse_stored_bullets for why no schema
    migration was needed). Returns
    {"bullets": list[str], "generated_at": iso8601 str, "has_data": bool}.
    """
    field = "recommendation_text" if audience == "student" else "teacher_recommendation_text"
    not_enough_data_text = NOT_ENOUGH_DATA_STUDENT if audience == "student" else NOT_ENOUGH_DATA_TEACHER
    framing = _STUDENT_FRAMING if audience == "student" else _TEACHER_FRAMING

    now = datetime.now(timezone.utc)
    context = _build_student_context(db, student)

    if context is None:
        setattr(student, field, json.dumps([not_enough_data_text]))
        student.recommendation_generated_at = now
        db.commit()
        return {"bullets": [not_enough_data_text], "generated_at": now.isoformat(), "has_data": False}

    prompt = f"{framing}\n\n{_GROUNDING_RULES}\n\nSTUDENT DATA (JSON):\n{context}\n"
    raw = _call_llm(prompt)

    bullets = _parse_llm_bullets(raw) if raw is not None else None

    if bullets is None:
        # LLM call failed OR returned unparseable output: keep whatever was
        # already cached rather than overwriting it with nothing/garbage —
        # the triggering interaction-recording flow must not crash or lose
        # the previous good value either way.
        if raw is not None:
            logger.warning(
                f"Recommendation LLM output could not be parsed as bullets "
                f"for student={student.student_id[:12]}..."
            )
        existing, _ = _parse_stored_bullets(getattr(student, field, None), not_enough_data_text)
        return {"bullets": existing, "generated_at": now.isoformat(), "has_data": True}

    setattr(student, field, json.dumps(bullets))
    student.recommendation_generated_at = now
    db.commit()
    return {"bullets": bullets, "generated_at": now.isoformat(), "has_data": True}


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

    Stored on CohortInsight.insight_text as a JSON-encoded array of bullet
    strings (Text column, same backward-compatible read path as per-student
    recommendations — see _parse_stored_bullets). Returns
    {"bullets": list[str], "generated_at": iso8601 str}.
    """
    now = datetime.now(timezone.utc)
    context = _build_cohort_context(db)

    row = db.get(CohortInsight, 1)
    if row is None:
        row = CohortInsight(
            id=1, insight_text=json.dumps([NOT_ENOUGH_DATA_COHORT]),
            generated_at=now, based_on_total_attempts=0,
        )
        db.add(row)

    if context is None:
        row.insight_text = json.dumps([NOT_ENOUGH_DATA_COHORT])
        row.generated_at = now
        row.based_on_total_attempts = 0
        db.commit()
        return {"bullets": [NOT_ENOUGH_DATA_COHORT], "generated_at": now.isoformat()}

    prompt = f"{_COHORT_FRAMING}\n\n{_GROUNDING_RULES}\n\nCOHORT DATA (JSON):\n{context}\n"
    raw = _call_llm(prompt)
    bullets = _parse_llm_bullets(raw) if raw is not None else None

    if bullets is None:
        if raw is not None:
            logger.warning("Cohort insight LLM output could not be parsed as bullets")
        # Keep the previous cached insight rather than overwriting with nothing/garbage.
        db.commit()
        existing, _ = _parse_stored_bullets(row.insight_text, NOT_ENOUGH_DATA_COHORT)
        return {"bullets": existing, "generated_at": row.generated_at.isoformat()}

    row.insight_text = json.dumps(bullets)
    row.generated_at = now
    row.based_on_total_attempts = context["total_questions_answered"]
    db.commit()
    return {"bullets": bullets, "generated_at": now.isoformat()}


def get_cohort_insight_bullets(db: Session) -> list[str]:
    """Read-only accessor for the dashboard API — never triggers generation."""
    row = db.get(CohortInsight, 1)
    bullets, _ = _parse_stored_bullets(row.insight_text if row else None, NOT_ENOUGH_DATA_COHORT)
    return bullets
