"""
api/routes/ussd.py — USSD inbound webhook handler.

Implements FR-CI-02, FR-CI-05, FR-CI-06, FR-CI-07, FR-FSM-04.
"""
from fastapi import APIRouter, Request, Response, Depends
from sqlalchemy.orm import Session as DBSession
from db.database import get_db
from utils.logger import get_logger
from utils.phone import normalise_phone, phone_to_student_id
from utils.response_formatter import format_ussd_response
from fsm.dialogue_manager import handle_message

router = APIRouter(prefix="/webhook", tags=["ussd"])
logger = get_logger(__name__)


@router.post("/ussd")
async def ussd_webhook(request: Request, db: DBSession = Depends(get_db)):
    form = await request.form()
    session_id = form.get("sessionId", "")
    phone_number = form.get("phoneNumber", "")
    network_code = form.get("networkCode", "")
    service_code = form.get("serviceCode", "")
    text = form.get("text", "")

    # Africa's Talking sends cumulative text "1*2*3". Take the last segment.
    last_input = text.split("*")[-1] if text else ""

    e164 = normalise_phone(phone_number)
    student_id = phone_to_student_id(phone_number)

    logger.info(
        f"USSD inbound | session={session_id} | student={student_id[:12]}... "
        f"| cumulative={text!r} last={last_input!r}"
    )

    result = handle_message(db, student_id, "ussd", last_input)

    reply = format_ussd_response(result.response, end_session=result.end_session)
    return Response(content=reply, media_type="text/plain")
