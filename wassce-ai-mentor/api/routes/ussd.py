"""
api/routes/ussd.py — USSD inbound webhook handler.

Implements FR-CI-02, FR-CI-05, FR-CI-06, FR-CI-07, FR-FSM-04.
"""
from fastapi import APIRouter, Request, Response, Depends
from sqlalchemy.orm import Session
from db.database import get_db
from utils.logger import get_logger
from utils.phone import normalise_phone, phone_to_student_id
from utils.response_formatter import format_ussd_response

router = APIRouter(prefix="/webhook", tags=["ussd"])
logger = get_logger(__name__)


@router.post("/ussd")
async def ussd_webhook(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    session_id = form.get("sessionId", "")
    phone_number = form.get("phoneNumber", "")
    network_code = form.get("networkCode", "")
    service_code = form.get("serviceCode", "")
    text = form.get("text", "")

    e164 = normalise_phone(phone_number)
    student_id = phone_to_student_id(phone_number)

    logger.info(
        f"USSD inbound | session={session_id} | student={student_id[:12]}... "
        f"| text={text!r} | network={network_code}"
    )

    # Placeholder menu — FSM will replace this in Step 5
    body = (
        "Welcome to WASSCE AI Mentor.\n"
        "1. Start Learning\n"
        "2. Take Test\n"
        "3. My Progress"
    )
    reply = format_ussd_response(body, end_session=False)
    return Response(content=reply, media_type="text/plain")
