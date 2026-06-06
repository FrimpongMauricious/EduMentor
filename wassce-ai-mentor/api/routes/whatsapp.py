"""
api/routes/whatsapp.py — WhatsApp inbound webhook handler.

Implements FR-CI-01, FR-CI-03, FR-CI-04, FR-CI-06, FR-CI-07.
"""
from fastapi import APIRouter, Request, Response, Depends
from sqlalchemy.orm import Session
from db.database import get_db
from utils.logger import get_logger
from utils.phone import normalise_phone, phone_to_student_id
from utils.twilio_validator import validate_twilio_signature
from utils.response_formatter import format_whatsapp_response, to_twiml

router = APIRouter(prefix="/webhook", tags=["whatsapp"])
logger = get_logger(__name__)


@router.post("/whatsapp")
async def whatsapp_webhook(request: Request, db: Session = Depends(get_db)):
    # FR-CI-03: validate Twilio signature
    await validate_twilio_signature(request)

    # Parse inbound form data
    form = await request.form()
    from_number = form.get("From", "")
    body = (form.get("Body") or "").strip()

    # FR-CI-07: normalise to E.164 and FR-SM-01: hash
    e164 = normalise_phone(from_number)
    student_id = phone_to_student_id(from_number)

    logger.info(f"WhatsApp inbound | student={student_id[:12]}... | body={body!r}")

    # Placeholder response — FSM and RAG will replace this in later steps
    reply = format_whatsapp_response(
        "Hello! WASSCE AI Mentor is being built. Full functionality coming soon."
    )

    twiml = to_twiml(reply)
    return Response(content=twiml, media_type="application/xml")
