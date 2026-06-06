from db.database import get_db
from sqlalchemy.orm import Session
from fastapi import Depends


def get_database() -> Session:
    """Dependency injection for database session."""
    return next(get_db())
