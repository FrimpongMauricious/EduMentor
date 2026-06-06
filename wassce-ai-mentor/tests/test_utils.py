"""
tests/test_utils.py — Unit tests for utility modules.
"""
import pytest
from utils.phone import normalise_phone, hash_phone, phone_to_student_id
from utils.response_formatter import format_whatsapp_response, format_ussd_response, to_twiml


class TestPhoneNormalisation:
    def test_local_format_with_leading_zero(self):
        assert normalise_phone("0241234567") == "+233241234567"

    def test_already_e164(self):
        assert normalise_phone("+233241234567") == "+233241234567"

    def test_without_plus_with_country_code(self):
        assert normalise_phone("233241234567") == "+233241234567"

    def test_strips_whatsapp_prefix(self):
        assert normalise_phone("whatsapp:+233241234567") == "+233241234567"

    def test_strips_spaces_and_dashes(self):
        assert normalise_phone("024-123 4567") == "+233241234567"

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            normalise_phone("")


class TestPhoneHashing:
    def test_hash_is_64_chars(self):
        assert len(hash_phone("+233241234567")) == 64

    def test_hash_is_deterministic(self):
        h1 = hash_phone("+233241234567")
        h2 = hash_phone("+233241234567")
        assert h1 == h2

    def test_different_numbers_give_different_hashes(self):
        assert hash_phone("+233241234567") != hash_phone("+233241234568")

    def test_pipeline_normalises_then_hashes(self):
        # Same number expressed two ways should yield the same student_id
        a = phone_to_student_id("0241234567")
        b = phone_to_student_id("+233241234567")
        assert a == b


class TestResponseFormatter:
    def test_whatsapp_short_message_unchanged(self):
        msg = "Hello!"
        assert format_whatsapp_response(msg) == msg

    def test_whatsapp_long_message_truncated(self):
        msg = "x" * 1500
        out = format_whatsapp_response(msg)
        assert len(out) == 1024
        assert out.endswith("...")

    def test_ussd_con_prefix(self):
        out = format_ussd_response("Hello", end_session=False)
        assert out.startswith("CON ")

    def test_ussd_end_prefix(self):
        out = format_ussd_response("Goodbye", end_session=True)
        assert out.startswith("END ")

    def test_ussd_never_exceeds_182_chars(self):
        long_body = "x" * 500
        out = format_ussd_response(long_body)
        assert len(out) <= 182

    def test_twiml_escapes_xml(self):
        out = to_twiml("Hello & <world>")
        assert "&amp;" in out
        assert "&lt;" in out
        assert "&gt;" in out
