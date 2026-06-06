"""
utils/twilio_validator.py — Validates Twilio webhook signatures (FR-CI-03, NFR-SEC-02).
"""
from twilio.request_validator import RequestValidator
from fastapi import Request, HTTPException
from config import get_settings


async def validate_twilio_signature(request: Request) -> None:
    """
    Raise HTTPException 403 if the X-Twilio-Signature header is missing or invalid.
    Skipped in development mode unless the auth token is set.
    """
    settings = get_settings()

    # Skip validation in development if no token configured
    if settings.app_env == "development" and not settings.twilio_auth_token:
        return

    signature = request.headers.get("X-Twilio-Signature", "")
    if not signature:
        raise HTTPException(status_code=403, detail="Missing Twilio signature")

    validator = RequestValidator(settings.twilio_auth_token)
    url = str(request.url)
    form_data = await request.form()
    form_dict = dict(form_data)

    if not validator.validate(url, form_dict, signature):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")
