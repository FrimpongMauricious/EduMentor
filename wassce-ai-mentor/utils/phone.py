"""
utils/phone.py — Phone number normalisation and hashing.

Per FR-SM-01: student identity is stored as SHA-256 hash of E.164 phone number.
Per FR-CI-07: all inbound phone numbers normalised to E.164 (+233XXXXXXXXX) before processing.
"""
import hashlib
import re


def normalise_phone(raw_phone: str) -> str:
    """
    Normalise a phone number to E.164 format.
    Handles common Ghanaian variants:
      - 0241234567        -> +233241234567
      - 233241234567      -> +233241234567
      - +233241234567     -> +233241234567
      - whatsapp:+233...  -> +233...
    """
    if not raw_phone:
        raise ValueError("Phone number cannot be empty")

    # Strip 'whatsapp:' prefix if present (Twilio sends 'whatsapp:+233...')
    phone = raw_phone.replace("whatsapp:", "").strip()

    # Remove all non-digit and non-plus characters
    phone = re.sub(r"[^\d+]", "", phone)

    if phone.startswith("+"):
        return phone
    if phone.startswith("00"):
        return "+" + phone[2:]
    if phone.startswith("0"):
        return "+233" + phone[1:]
    if phone.startswith("233"):
        return "+" + phone
    # If 9 digits, assume Ghana mobile without prefix
    if len(phone) == 9:
        return "+233" + phone
    return "+" + phone


def hash_phone(e164_phone: str) -> str:
    """Return SHA-256 hex digest of the E.164 phone number."""
    return hashlib.sha256(e164_phone.encode("utf-8")).hexdigest()


def phone_to_student_id(raw_phone: str) -> str:
    """Normalise + hash in one step. Returns the student_id used throughout the system."""
    return hash_phone(normalise_phone(raw_phone))
