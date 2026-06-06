"""
api/routes/whatsapp.py — WhatsApp inbound webhook handler.

Implements FR-CI-01, FR-CI-03, FR-CI-04, FR-CI-06, FR-CI-07.
Wired to the FSM dialogue manager.
"""
from fastapi import APIRouter, Request, Response, Depends
from sqlalchemy.orm import Session as DBSession
from db.database import get_db
from utils.logger import get_logger
from utils.phone import normalise_phone, phone_to_student_id
from utils.twilio_validator import validate_twilio_signature
from utils.response_formatter import format_whatsapp_response, to_twiml
from fsm.dialogue_manager import handle_message

router = APIRouter(prefix="/webhook", tags=["whatsapp"])
logger = get_logger(__name__)


@router.post("/whatsapp")
async def whatsapp_webhook(request: Request, db: DBSession = Depends(get_db)):
    await validate_twilio_signature(request)

    form = await request.form()
    from_number = form.get("From", "")
    body = (form.get("Body") or "").strip()

    e164 = normalise_phone(from_number)
    student_id = phone_to_student_id(from_number)

    logger.info(f"WhatsApp inbound | student={student_id[:12]}... | body={body!r}")

    result = handle_message(db, student_id, "whatsapp", body)

    reply = format_whatsapp_response(result.response)
    twiml = to_twiml(reply)
    return Response(content=twiml, media_type="application/xml")
