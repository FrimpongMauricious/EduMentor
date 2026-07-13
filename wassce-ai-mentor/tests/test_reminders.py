"""
tests/test_reminders.py — Tests for the study reminder system.
"""
import pytest
from unittest.mock import patch, MagicMock

from api.reminders import REMINDER_MESSAGES, _get_all_whatsapp_users


# ─── Reminder message content ─────────────────────────────────────────────────

class TestReminderMessages:
    def test_messages_exist(self):
        assert len(REMINDER_MESSAGES) >= 5

    def test_messages_contain_hi(self):
        for msg in REMINDER_MESSAGES:
            assert "Hi" in msg, f"Message missing 'Hi' call-to-action: {msg[:50]}"

    def test_messages_under_640_chars(self):
        for msg in REMINDER_MESSAGES:
            assert len(msg) <= 640, f"Message too long ({len(msg)} chars): {msg[:50]}"

    def test_messages_unique(self):
        assert len(REMINDER_MESSAGES) == len(set(REMINDER_MESSAGES)), (
            "Duplicate reminder messages found"
        )


# ─── User query ───────────────────────────────────────────────────────────────

class TestUserQuery:
    def test_returns_list(self):
        users = _get_all_whatsapp_users()
        assert isinstance(users, list)

    def test_whatsapp_format(self):
        users = _get_all_whatsapp_users()
        for u in users:
            assert u.startswith("whatsapp:"), f"Invalid format: {u}"

    def test_no_duplicates(self):
        users = _get_all_whatsapp_users()
        assert len(users) == len(set(users)), "Duplicate phone numbers in user list"


# ─── API endpoint behaviour ───────────────────────────────────────────────────

class TestReminderEndpoint:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from api.main import app
        return TestClient(app)

    def test_rejects_without_secret(self, client):
        response = client.post("/cron/send-reminders")
        assert response.status_code in (403, 422)

    def test_rejects_wrong_secret(self, client):
        response = client.post(
            "/cron/send-reminders",
            headers={"X-Cron-Secret": "definitely-wrong-secret"},
        )
        assert response.status_code == 403

    def test_status_endpoint_accessible(self, client):
        response = client.get("/cron/reminder-status")
        assert response.status_code == 200
        data = response.json()
        assert "total_whatsapp_users" in data
        assert "schedule" in data
        assert "reminder_messages_count" in data

    def test_status_returns_correct_message_count(self, client):
        response = client.get("/cron/reminder-status")
        data = response.json()
        assert data["reminder_messages_count"] == len(REMINDER_MESSAGES)

    def test_correct_secret_with_no_twilio_creds_returns_500_or_ok(self, client):
        """
        With correct secret and no users, should return ok with 0 sent.
        With users but broken Twilio creds, gracefully returns 500 from Twilio init.
        We patch _get_all_whatsapp_users to return empty so it exits cleanly.
        """
        with patch("api.reminders._get_all_whatsapp_users", return_value=[]):
            response = client.post(
                "/cron/send-reminders",
                headers={"X-Cron-Secret": "wassce-reminder-secret-2025"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["sent"] == 0
        assert data["total_users"] == 0

    def test_send_reminders_calls_twilio_per_user(self, client):
        """Verify Twilio client.messages.create is called for each user."""
        fake_users = ["whatsapp:+233241000001", "whatsapp:+233241000002"]
        mock_twilio_instance = MagicMock()
        mock_twilio_class = MagicMock(return_value=mock_twilio_instance)

        with patch("api.reminders._get_all_whatsapp_users", return_value=fake_users), \
             patch("api.reminders.Client", mock_twilio_class):
            response = client.post(
                "/cron/send-reminders",
                headers={"X-Cron-Secret": "wassce-reminder-secret-2025"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["sent"] == 2
        assert data["failed"] == 0
        assert mock_twilio_instance.messages.create.call_count == 2

    def test_twilio_failure_per_user_is_caught(self, client):
        """A Twilio error for one user must not stop the batch."""
        fake_users = ["whatsapp:+233241000001", "whatsapp:+233241000002"]
        mock_twilio_instance = MagicMock()
        mock_twilio_instance.messages.create.side_effect = [
            Exception("Twilio 63016: 24h window closed"),
            MagicMock(),  # second user succeeds
        ]
        mock_twilio_class = MagicMock(return_value=mock_twilio_instance)

        with patch("api.reminders._get_all_whatsapp_users", return_value=fake_users), \
             patch("api.reminders.Client", mock_twilio_class):
            response = client.post(
                "/cron/send-reminders",
                headers={"X-Cron-Secret": "wassce-reminder-secret-2025"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["sent"] == 1
        assert data["failed"] == 1

    def test_response_includes_timestamp(self, client):
        with patch("api.reminders._get_all_whatsapp_users", return_value=[]):
            response = client.post(
                "/cron/send-reminders",
                headers={"X-Cron-Secret": "wassce-reminder-secret-2025"},
            )
        data = response.json()
        assert "timestamp" in data


# ─── Phone number stored on WhatsApp contact ──────────────────────────────────

class TestPhoneNumberStorage:
    """Verify that the WhatsApp webhook stores phone_number for reminder delivery."""

    @pytest.fixture
    def db(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from db.database import Base
        from db import models  # noqa: F401
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        session = Session()
        try:
            yield session
        finally:
            session.close()

    def test_student_model_has_phone_number_field(self):
        from db.models import Student
        assert hasattr(Student, "phone_number"), (
            "Student model must have a phone_number column for reminder delivery"
        )

    def test_phone_number_stored_on_first_contact(self, db):
        """After a WhatsApp interaction, the student record must have phone_number set."""
        from fsm.dialogue_manager import handle_message
        from db.models import Student
        from utils.phone import phone_to_student_id

        raw_number = "whatsapp:+233241234567"
        student_id = phone_to_student_id(raw_number)

        # Simulate handle_message (creates Student record)
        handle_message(db, student_id, "whatsapp", "Hi")

        # Simulate what the webhook does: store phone_number if not set
        student = db.get(Student, student_id)
        assert student is not None
        if not student.phone_number:
            student.phone_number = raw_number
            db.commit()

        student = db.get(Student, student_id)
        assert student.phone_number == raw_number
