"""
api/routes/dashboard.py — Read-only JSON API for the React performance dashboard.

Two audiences:
  - Students: look up their own record by phone number (rate limited).
  - Teachers: password-protected access to the full cohort, plus drill-down
    into any individual student.

Student identity is resolved via the same normalise_phone()/phone_to_student_id()
pipeline the WhatsApp and USSD webhooks already use, so a phone number typed on
the dashboard (in any of the accepted formats) resolves to exactly the same
student record regardless of which channel the student actually used.
"""
import math
import time
from collections import Counter, defaultdict
from datetime import datetime

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from ai.recommendations import (
    NOT_ENOUGH_DATA_STUDENT,
    NOT_ENOUGH_DATA_TEACHER,
    NOT_ENOUGH_DATA_COHORT,
    get_cohort_insight_bullets,
    _parse_stored_bullets,
)
from config import get_settings
from db.database import get_db
from db.models import Interaction, PerformanceVector, Session as SessionRow, Student
from utils.logger import get_logger
from utils.phone import normalise_phone, phone_to_student_id

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
logger = get_logger(__name__)

EXCLUDED_RESULTS = ("skip", "no_question")
VALID_SUBJECTS = {"maths", "english", "science", "social_studies"}

# ── Rate limiting (in-memory, per-IP) ───────────────────────────────────────
# A simple sliding-window counter is enough for a single-process student
# project deployment; it resets on process restart, which is acceptable here.
RATE_LIMIT_REQUESTS = 10
RATE_LIMIT_WINDOW_SECONDS = 60
_request_log: dict[str, list[float]] = defaultdict(list)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _rate_limited(ip: str) -> bool:
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    log = _request_log[ip]
    while log and log[0] < cutoff:
        log.pop(0)
    if len(log) >= RATE_LIMIT_REQUESTS:
        return True
    log.append(now)
    return False


def _error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


# ── Teacher auth ─────────────────────────────────────────────────────────────

def _teacher_authorized(x_dashboard_password: str | None) -> bool:
    settings = get_settings()
    return bool(x_dashboard_password) and x_dashboard_password == settings.dashboard_password


# ── Phone helpers ────────────────────────────────────────────────────────────

def _mask_phone(e164_phone: str) -> str:
    """+233531850867 -> ...XXX850867 (last 6 significant digits visible)."""
    digits = e164_phone.lstrip("+")
    if digits.startswith("233"):
        digits = digits[3:]
    tail = digits[-6:] if len(digits) >= 6 else digits
    return f"...XXX{tail}"


def _resolve_e164(student: Student, fallback: str | None) -> str | None:
    """Prefer the stored (Twilio-format) phone number; fall back to whatever
    the caller looked the student up with (always present for a matched lookup)."""
    if student.phone_number:
        try:
            return normalise_phone(student.phone_number)
        except ValueError:
            pass
    return fallback


# ── Data assembly ────────────────────────────────────────────────────────────

def _subject_breakdown(performance_vectors: list[PerformanceVector]) -> list[dict]:
    agg: dict[str, dict] = defaultdict(lambda: {"attempted": 0, "correct": 0})
    for pv in performance_vectors:
        row = agg[pv.subject]
        row["attempted"] += pv.attempts
        row["correct"] += pv.correct
    return [
        {
            "subject": subject,
            "attempted": row["attempted"],
            "correct": row["correct"],
            "accuracy": round(row["correct"] / row["attempted"] * 100, 1) if row["attempted"] else 0.0,
        }
        for subject, row in sorted(agg.items())
    ]


# ── Leaderboard ──────────────────────────────────────────────────────────────
#
# Ranking metric: the Wilson score interval lower bound on accuracy (95%
# confidence), NOT raw accuracy. Raw accuracy alone lets a student with a
# tiny number of lucky answers (e.g. 3/3 = 100%) outrank a student with
# substantially more evidence of real understanding (e.g. 95/100 = 95%) —
# exactly backwards. The Wilson lower bound answers "what's the accuracy
# this student's record is at LEAST consistent with, at 95% confidence?",
# which is naturally pulled toward 0 for small samples (wide uncertainty)
# and converges toward the raw accuracy as attempts grow (tight
# uncertainty) — so it rewards both correctness AND volume, in one
# principled formula, without needing to hand-tune a separate volume
# weight. This is the same interval used for Reddit's "best" comment
# ranking, for the same reason.
_WILSON_Z = 1.96  # 95% confidence


def _wilson_lower_bound(correct: int, attempts: int) -> float:
    """Lower bound of the Wilson score confidence interval for a binomial
    proportion (correct out of attempts), as a fraction in [0, 1]."""
    if attempts == 0:
        return 0.0
    p = correct / attempts
    n = attempts
    z2 = _WILSON_Z ** 2
    denominator = 1 + z2 / n
    center = p + z2 / (2 * n)
    margin = _WILSON_Z * math.sqrt((p * (1 - p) / n) + (z2 / (4 * n ** 2)))
    return (center - margin) / denominator


def _compute_leaderboard(db: DBSession, subject: str | None) -> list[dict]:
    """
    Rank every student with at least one answered question in scope
    (overall if subject is None, else that one subject) by
    _wilson_lower_bound, descending. Ties broken by more total attempts,
    then alphabetically by name, for a fully deterministic order.

    Students with zero attempts in scope are excluded entirely — not
    given a rank or a nonsensical 0% score — since a Wilson bound isn't
    meaningful with zero evidence either way.

    Reuses the same PerformanceVector rows _subject_breakdown() and
    /teacher/overview already aggregate from — no new query pattern.
    """
    query = select(PerformanceVector)
    if subject:
        query = query.where(PerformanceVector.subject == subject)
    pvs = db.execute(query).scalars().all()

    per_student: dict[str, dict] = defaultdict(lambda: {"attempts": 0, "correct": 0})
    for pv in pvs:
        row = per_student[pv.student_id]
        row["attempts"] += pv.attempts
        row["correct"] += pv.correct

    students_by_id = {s.student_id: s for s in db.execute(select(Student)).scalars().all()}

    rows = []
    for student_id, agg in per_student.items():
        if agg["attempts"] == 0:
            continue
        student = students_by_id.get(student_id)
        if student is None:
            continue
        rows.append({
            "student_id": student_id,
            "name": student.name or "Student",
            "attempts": agg["attempts"],
            "correct": agg["correct"],
            "accuracy": round(agg["correct"] / agg["attempts"] * 100, 1),
            "score": round(_wilson_lower_bound(agg["correct"], agg["attempts"]) * 100, 1),
        })

    rows.sort(key=lambda r: (-r["score"], -r["attempts"], r["name"].lower()))
    for idx, row in enumerate(rows, start=1):
        row["rank"] = idx
    return rows


def _first_name(full_name: str) -> str:
    parts = (full_name or "Student").strip().split()
    return parts[0] if parts else "Student"


def _masked_display_names(rows: list[dict]) -> dict[str, str]:
    """
    Map student_id -> masked display name for a set of ranked rows:
    first name only, upgraded to "First L." when that first name collides
    with another student's in this same result set (so two "Ama"s don't
    render as indistinguishable rows).
    """
    first_names = {row["student_id"]: _first_name(row["name"]) for row in rows}
    counts = Counter(fn.lower() for fn in first_names.values())

    display = {}
    for row in rows:
        first = first_names[row["student_id"]]
        if counts[first.lower()] > 1:
            parts = (row["name"] or "Student").strip().split()
            last_initial = f" {parts[-1][0].upper()}." if len(parts) > 1 else ""
            display[row["student_id"]] = f"{first}{last_initial}"
        else:
            display[row["student_id"]] = first
    return display


def _student_detail(
    db: DBSession,
    student: Student,
    fallback_e164: str | None = None,
    audience: str = "student",
) -> dict:
    pvs = db.execute(
        select(PerformanceVector).where(PerformanceVector.student_id == student.student_id)
    ).scalars().all()

    total_attempts = sum(pv.attempts for pv in pvs)
    total_correct = sum(pv.correct for pv in pvs)
    overall_accuracy = round(total_correct / total_attempts * 100, 1) if total_attempts else 0.0

    recent_rows = db.execute(
        select(Interaction, SessionRow.current_subject)
        .join(SessionRow, Interaction.session_id == SessionRow.session_id)
        .where(
            Interaction.student_id == student.student_id,
            Interaction.evaluation_result.is_not(None),
            Interaction.evaluation_result.not_in(EXCLUDED_RESULTS),
        )
        .order_by(Interaction.timestamp.desc())
        .limit(20)
    ).all()
    recent_activity = [
        {
            "subject": subject or "unknown",
            "score": 1 if interaction.evaluation_result == "correct" else 0,
            "timestamp": interaction.timestamp.isoformat(),
        }
        for interaction, subject in recent_rows
    ]

    e164 = _resolve_e164(student, fallback_e164)

    if audience == "teacher":
        recommendation, recommendation_has_data = _parse_stored_bullets(
            student.teacher_recommendation_text, NOT_ENOUGH_DATA_TEACHER
        )
    else:
        recommendation, recommendation_has_data = _parse_stored_bullets(
            student.recommendation_text, NOT_ENOUGH_DATA_STUDENT
        )

    return {
        "name": student.name or "Student",
        "phone_masked": _mask_phone(e164) if e164 else "unknown",
        "total_questions": total_attempts,
        "overall_accuracy": overall_accuracy,
        "by_subject": _subject_breakdown(pvs),
        "recent_activity": recent_activity,
        "last_active": student.last_seen_at.isoformat() if student.last_seen_at else None,
        "recommendation": recommendation,
        "recommendation_has_data": recommendation_has_data,
    }


def _lookup_student(db: DBSession, raw_phone: str) -> tuple[Student | None, str | None]:
    try:
        e164 = normalise_phone(raw_phone)
    except ValueError:
        return None, None
    student = db.get(Student, phone_to_student_id(raw_phone))
    return student, e164


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/student/{phone}")
async def student_dashboard(phone: str, request: Request, db: DBSession = Depends(get_db)):
    if _rate_limited(_client_ip(request)):
        return _error(429, "Too many requests. Please wait a moment and try again.")

    student, e164 = _lookup_student(db, phone)
    if not student:
        return _error(404, "No student found with that number")

    return _student_detail(db, student, fallback_e164=e164)  # audience="student" (default) — shared with Guardian


@router.get("/leaderboard")
async def leaderboard(
    request: Request,
    subject: str | None = None,
    phone: str | None = None,
    db: DBSession = Depends(get_db),
):
    """
    Student/Guardian-facing leaderboard. `subject` omitted = overall;
    otherwise one of VALID_SUBJECTS. `phone` (optional) is the viewer's own
    number — used only to identify and unmask their own row; every other
    row is shown first-name-only (see _masked_display_names). Reuses the
    same rate limiting as the student lookup endpoint since it's just as
    public (no password).
    """
    if _rate_limited(_client_ip(request)):
        return _error(429, "Too many requests. Please wait a moment and try again.")
    if subject is not None and subject not in VALID_SUBJECTS:
        return _error(400, "Unknown subject.")

    rows = _compute_leaderboard(db, subject)
    display_names = _masked_display_names(rows)

    viewer_student_id = None
    if phone:
        try:
            normalise_phone(phone)  # validate shape; ignore the normalised value itself
            viewer_student_id = phone_to_student_id(phone)
        except ValueError:
            viewer_student_id = None

    rankings = []
    viewer = None
    for row in rows:
        is_you = viewer_student_id is not None and row["student_id"] == viewer_student_id
        entry = {
            "rank": row["rank"],
            "name": row["name"] if is_you else display_names[row["student_id"]],
            "is_you": is_you,
            "score": row["score"],
            "accuracy": row["accuracy"],
            "attempts": row["attempts"],
        }
        rankings.append(entry)
        if is_you:
            viewer = {"has_data": True, **entry}

    if viewer_student_id is not None and viewer is None:
        viewer = {"has_data": False}  # a real viewer, but zero attempts in this scope

    return {
        "subject": subject,
        "rankings": rankings,
        "viewer": viewer,
        "total_ranked": len(rankings),
    }


@router.get("/teacher/overview")
async def teacher_overview(
    db: DBSession = Depends(get_db),
    x_dashboard_password: str | None = Header(default=None),
):
    if not _teacher_authorized(x_dashboard_password):
        return _error(403, "Incorrect or missing dashboard password.")

    students = db.execute(select(Student)).scalars().all()
    pvs = db.execute(select(PerformanceVector)).scalars().all()

    total_attempts = sum(pv.attempts for pv in pvs)
    total_correct = sum(pv.correct for pv in pvs)
    overall_accuracy = round(total_correct / total_attempts * 100, 1) if total_attempts else 0.0

    per_student: dict[str, dict] = defaultdict(lambda: {"attempted": 0, "correct": 0})
    for pv in pvs:
        row = per_student[pv.student_id]
        row["attempted"] += pv.attempts
        row["correct"] += pv.correct

    students_sorted = sorted(
        students, key=lambda s: s.last_seen_at or datetime.min, reverse=True
    )
    student_rows = []
    for s in students_sorted:
        row = per_student.get(s.student_id, {"attempted": 0, "correct": 0})
        accuracy = round(row["correct"] / row["attempted"] * 100, 1) if row["attempted"] else 0.0
        e164 = _resolve_e164(s, None)
        student_rows.append({
            "name": s.name or "Student",
            "phone": e164,  # unmasked — teacher is authenticated; used for drill-down navigation
            "phone_masked": _mask_phone(e164) if e164 else "unknown",
            "total_questions": row["attempted"],
            "accuracy": accuracy,
            "last_active": s.last_seen_at.isoformat() if s.last_seen_at else None,
        })

    insights = get_cohort_insight_bullets(db)
    return {
        "total_students": len(students),
        "total_questions_answered": total_attempts,
        "overall_accuracy": overall_accuracy,
        "by_subject": _subject_breakdown(pvs),
        "students": student_rows,
        "insights": insights,
        "insights_has_data": insights != [NOT_ENOUGH_DATA_COHORT],
    }


@router.get("/teacher/student/{phone}")
async def teacher_student_detail(
    phone: str,
    db: DBSession = Depends(get_db),
    x_dashboard_password: str | None = Header(default=None),
):
    if not _teacher_authorized(x_dashboard_password):
        return _error(403, "Incorrect or missing dashboard password.")

    student, e164 = _lookup_student(db, phone)
    if not student:
        return _error(404, "No student found with that number")

    return _student_detail(db, student, fallback_e164=e164, audience="teacher")


@router.get("/teacher/leaderboard")
async def teacher_leaderboard(
    subject: str | None = None,
    db: DBSession = Depends(get_db),
    x_dashboard_password: str | None = Header(default=None),
):
    """
    Teacher-facing leaderboard: same ranking as GET /leaderboard, but full
    names throughout, no masking. Consistent with every other teacher
    endpoint in this file (/teacher/overview, /teacher/student/{phone}),
    which already show full names — masking here would be a strictly less
    private but more confusing experience for a viewer who already has
    full access to every student's name elsewhere in the same app.
    """
    if not _teacher_authorized(x_dashboard_password):
        return _error(403, "Incorrect or missing dashboard password.")
    if subject is not None and subject not in VALID_SUBJECTS:
        return _error(400, "Unknown subject.")

    rows = _compute_leaderboard(db, subject)
    rankings = [
        {
            "rank": row["rank"],
            "name": row["name"],
            "score": row["score"],
            "accuracy": row["accuracy"],
            "attempts": row["attempts"],
        }
        for row in rows
    ]
    return {"subject": subject, "rankings": rankings, "total_ranked": len(rankings)}
