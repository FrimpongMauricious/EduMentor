"""
api/routes/whatsapp.py — WhatsApp inbound webhook handler.

Implements FR-CI-01, FR-CI-03, FR-CI-04, FR-CI-06, FR-CI-07.
Wired to the FSM dialogue manager.
"""
from fastapi import APIRouter, Request, Response, Depends
from sqlalchemy.orm import Session as DBSession
from db.database import get_db
from db.models import Student
from utils.logger import get_logger
from utils.phone import phone_to_student_id, is_valid_phone
from utils.twilio_validator import validate_twilio_signature
from utils.response_formatter import format_whatsapp_response, to_twiml
from fsm.dialogue_manager import handle_message

router = APIRouter(prefix="/webhook", tags=["whatsapp"])
logger = get_logger(__name__)

# Shown when Twilio's "From" value fails phone validation (see the guard
# below) — we have no student identity to reply *as*, so this is generic.
UNRECOGNISED_SENDER_REPLY = "Sorry, we couldn't process your message right now. Please try again in a moment."


@router.post("/whatsapp")
async def whatsapp_webhook(request: Request, db: DBSession = Depends(get_db)):
    await validate_twilio_signature(request)

    form = await request.form()
    from_number = form.get("From", "")
    body = (form.get("Body") or "").strip()

    # Twilio can occasionally deliver a non-numeric "From" identifier instead
    # of a real phone number (WhatsApp linked-device/business-account quirk
    # on the sender's side — see commit 51a1c6b, e.g. "whatsapp:GH.214237...").
    # student_id = sha256(normalise_phone(from_number)), so hashing a garbled
    # value here would still mint a brand-new, permanent student_id — forking
    # a real (possibly existing) student's data across two records, which is
    # exactly the bug 51a1c6b left unfixed (it only stopped the bad value
    # from being *stored* as phone_number, not from being *hashed* into a
    # new identity in the first place).
    #
    # There's no better signal to fall back on: this webhook only receives
    # Twilio's standard WhatsApp form fields (From/Body), and the codebase
    # has no session/cookie/conversation mechanism that identifies a sender
    # independently of their phone number. So when "From" doesn't look like
    # a real number, we don't create or route to ANY student_id — we just
    # decline the message with a generic reply. This trades a dropped
    # message (the student re-sends, usually with a valid "From" next time —
    # the quirk has been transient in every case seen in production logs)
    # for the alternative of silently forking someone's identity, which is
    # unrecoverable without a manual merge (scripts/merge_duplicate_student.py).
    if not is_valid_phone(from_number):
        logger.warning(
            "Rejected WhatsApp inbound: 'From' value failed phone validation "
            "(non-standard identifier, raw value not logged). No student "
            "identity was created or looked up; replying with a generic message."
        )
        twiml = to_twiml(UNRECOGNISED_SENDER_REPLY)
        return Response(content=twiml, media_type="application/xml")

    student_id = phone_to_student_id(from_number)

    logger.info(f"WhatsApp inbound | student={student_id[:12]}... | body={body!r}")

    result = handle_message(db, student_id, "whatsapp", body)

    # Store the Twilio-format phone number so the reminder system can reach this user.
    # from_number is already in "whatsapp:+233..." format — exactly what Twilio needs for outbound.
    # from_number is already confirmed valid above, so this write is unconditional.
    student = db.get(Student, student_id)
    if student and not student.phone_number:
        student.phone_number = from_number
        db.commit()

    reply = format_whatsapp_response(result.response)
    twiml = to_twiml(reply)
    return Response(content=twiml, media_type="application/xml")
