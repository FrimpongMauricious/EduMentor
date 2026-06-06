"""
config.py — Centralised application settings loaded from environment variables.
Uses pydantic-settings for type validation and .env file loading.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_number: str = "whatsapp:+14155238886"

    # Africa's Talking
    at_username: str = "sandbox"
    at_api_key: str = ""
    at_shortcode: str = ""

    # Groq LLM
    groq_api_key: str = ""
    groq_model: str = "llama3-8b-8192"

    # Database
    database_url: str = "sqlite:///./wassce_mentor.db"

    # ChromaDB
    chroma_persist_dir: str = "./chroma_store"
    chroma_collection_name: str = "wassce_qa"

    # Dashboard
    dashboard_password: str = "changeme"

    # App
    app_env: str = "development"
    base_url: str = "http://localhost:8000"

    # Constants (not loaded from env)
    USSD_MAX_CHARS: int = 182
    WHATSAPP_MAX_CHARS: int = 1024
    SESSION_TIMEOUT_MINUTES: int = 30
    QUESTION_HISTORY_LENGTH: int = 10
    DIFFICULTY_ADVANCE_THRESHOLD: float = 0.70


@lru_cache()
def get_settings() -> Settings:
    return Settings()
