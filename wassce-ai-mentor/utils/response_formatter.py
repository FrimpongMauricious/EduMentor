"""
utils/response_formatter.py — Format outbound messages per channel character limits.

USSD: max 182 chars (FR-RAG-24).
WhatsApp: max 1024 chars per message (FR-RAG-23).
"""
from config import get_settings

settings = get_settings()


def format_whatsapp_response(body: str) -> str:
    """Truncate to WhatsApp max chars with an ellipsis if needed."""
    max_len = settings.WHATSAPP_MAX_CHARS
    if len(body) <= max_len:
        return body
    return body[: max_len - 3] + "..."


def format_ussd_response(body: str, end_session: bool = False) -> str:
    """
    Format a USSD response with CON or END prefix.
    CON = continue session, END = terminate session.
    Total length including prefix must not exceed 182 chars (FR-RAG-24).
    """
    prefix = "END " if end_session else "CON "
    max_body = settings.USSD_MAX_CHARS - len(prefix)
    if len(body) > max_body:
        body = body[: max_body - 3] + "..."
    return prefix + body


def to_twiml(message: str) -> str:
    """Wrap a message in valid TwiML XML for Twilio's webhook response."""
    safe = (
        message.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{safe}</Message></Response>'
