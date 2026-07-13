"""
api/reminders.py — Study reminder endpoint.

Triggered by cron-job.org at 6AM, 8AM, 12PM, 4PM, 8PM, 10PM Ghana time (GMT+0).
Sends a WhatsApp reminder to all WhatsApp users whose phone number is on record.

WhatsApp 24-hour window constraint: messages to users who haven't messaged in the
last 24h will be rejected by Twilio. These failures are caught, logged, and skipped —
the batch continues for all other users.
"""
import os
import random
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException
from twilio.rest import Client

from db.database import get_session
from db.models import Student
from utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["cron"])

CRON_SECRET = os.getenv("CRON_SECRET", "wassce-reminder-secret-2025")

TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")

REMINDER_MESSAGES = [
    "📚 Time to practise! Your WASSCE exams are approaching. Send 'Hi' to start a quick session. You've got this! 💪",
    "🎯 Quick reminder: A few questions a day keeps exam stress away! Send 'Hi' to practise now.",
    "⭐ Don't break your study streak! Send 'Hi' to tackle some WASSCE questions right now.",
    "📖 Knowledge is power! Take 5 minutes to practise WASSCE questions. Send 'Hi' to begin.",
    "🔥 Champions practise daily! Send 'Hi' to sharpen your WASSCE skills now.",
    "💡 Small daily progress leads to big exam results. Send 'Hi' to start practising!",
    "🏆 Your future self will thank you for studying today. Send 'Hi' to begin!",
    "📝 WASSCE preparation tip: Consistency beats cramming. Send 'Hi' for a quick session!",
]


def _get_all_whatsapp_users() -> list[str]:
    """
    Return all distinct WhatsApp phone numbers stored in the students table.

    Phone numbers are in Twilio format ("whatsapp:+233XXXXXXXXX") and are stored
    when a user first sends a message to the bot via the WhatsApp webhook.
    Users who have never messaged will not have a phone_number on record.
    """
    db = get_session()
    try:
        rows = (
            db.query(Student.phone_number)
            .filter(
                Student.channel == "whatsapp",
                Student.phone_number.isnot(None),
            )
            .distinct()
            .all()
        )
        return [row[0] for row in rows if row[0]]
    finally:
        db.close()


@router.post("/cron/send-reminders")
async def send_reminders(x_cron_secret: str = Header(None)):
    """
    Send study reminders to all known WhatsApp users.
    Protected by X-Cron-Secret header (set in cron-job.org request headers).

    WhatsApp 24h window: failures for inactive users are expected and are
    caught per-user so the batch continues for all other users.
    """
    if x_cron_secret != CRON_SECRET:
        raise HTTPException(status_code=403, detail="Invalid cron secret")

    users = _get_all_whatsapp_users()

    if not users:
        logger.info("Reminder batch: no WhatsApp users with stored phone numbers yet")
        return {
            "status": "ok",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sent": 0,
            "failed": 0,
            "total_users": 0,
        }

    message = random.choice(REMINDER_MESSAGES)

    try:
        client = Client(TWILIO_SID, TWILIO_TOKEN)
    except Exception as e:
        logger.error(f"Twilio client init failed: {e}")
        raise HTTPException(status_code=500, detail="Twilio client error")

    sent = 0
    failed = 0
    errors = []

    for user_number in users:
        try:
            client.messages.create(
                body=message,
                from_=TWILIO_WHATSAPP,
                to=user_number,
            )
            sent += 1
            logger.info(f"Reminder sent to {user_number[:20]}...")
        except Exception as e:
            failed += 1
            error_msg = str(e)
            logger.warning(f"Reminder failed for {user_number[:20]}...: {error_msg[:120]}")
            errors.append({"user": user_number[:15] + "...", "error": error_msg[:100]})

    timestamp = datetime.now(timezone.utc).isoformat()
    logger.info(f"Reminder batch complete: {sent} sent, {failed} failed / {len(users)} users")

    result = {
        "status": "ok",
        "timestamp": timestamp,
        "message_used": message[:50] + "...",
        "total_users": len(users),
        "sent": sent,
        "failed": failed,
    }
    if 0 < failed <= 10:
        result["errors"] = errors

    return result


@router.get("/cron/reminder-status")
async def reminder_status():
    """Check how many users would receive reminders. No auth required."""
    users = _get_all_whatsapp_users()
    return {
        "total_whatsapp_users": len(users),
        "sample": [u[:15] + "..." for u in users[:5]],
        "reminder_messages_count": len(REMINDER_MESSAGES),
        "schedule": "6AM, 8AM, 12PM, 4PM, 8PM, 10PM GMT+0",
    }
