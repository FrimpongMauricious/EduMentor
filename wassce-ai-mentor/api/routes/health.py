"""
api/routes/health.py — Health check endpoint (FR not numbered; supports keep-alive cron).
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime, timezone
from db.database import get_db
from utils.logger import get_logger

router = APIRouter(tags=["health"])
logger = get_logger(__name__)


@router.get("/health")
async def health(db: Session = Depends(get_db)):
    db_status = "connected"
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        db_status = f"error: {e}"
        logger.error(f"Health check DB failure: {e}")

    return {
        "status": "ok",
        "service": "WASSCE AI Mentor",
        "version": "1.0.0",
        "db": db_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
