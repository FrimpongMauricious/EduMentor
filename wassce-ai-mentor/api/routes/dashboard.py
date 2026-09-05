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
import time
from collections import defaultdict
from datetime import datetime

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from ai.recommendations import NOT_ENOUGH_DATA_STUDENT, NOT_ENOUGH_DATA_TEACHER, get_cohort_insight_text
from config import get_settings
from db.database import get_db
from db.models import Interaction, PerformanceVector, Session as SessionRow, Student
from utils.logger import get_logger
from utils.phone import normalise_phone, phone_to_student_id

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
logger = get_logger(__name__)

EXCLUDED_RESULTS = ("skip", "no_question")

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
        recommendation = student.teacher_recommendation_text or NOT_ENOUGH_DATA_TEACHER
    else:
        recommendation = student.recommendation_text or NOT_ENOUGH_DATA_STUDENT

    return {
        "name": student.name or "Student",
        "phone_masked": _mask_phone(e164) if e164 else "unknown",
        "total_questions": total_attempts,
        "overall_accuracy": overall_accuracy,
        "by_subject": _subject_breakdown(pvs),
        "recent_activity": recent_activity,
        "last_active": student.last_seen_at.isoformat() if student.last_seen_at else None,
        "recommendation": recommendation,
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

    return {
        "total_students": len(students),
        "total_questions_answered": total_attempts,
        "overall_accuracy": overall_accuracy,
        "by_subject": _subject_breakdown(pvs),
        "students": student_rows,
        "insights": get_cohort_insight_text(db),
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
