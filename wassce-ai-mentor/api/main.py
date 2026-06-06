"""
api/main.py — FastAPI application entry point.
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from db.database import init_db
from api.routes import whatsapp, ussd, health
from utils.logger import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting WASSCE AI Mentor...")
    init_db()
    logger.info("Database ready.")
    yield
    logger.info("Shutting down WASSCE AI Mentor.")


app = FastAPI(
    title="WASSCE AI Mentor",
    description="Multi-channel adaptive RAG-based tutor for WASSCE candidates.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(whatsapp.router)
app.include_router(ussd.router)


@app.get("/", tags=["root"])
async def root():
    return {
        "service": "WASSCE AI Mentor",
        "status": "online",
        "docs": "/docs",
        "health": "/health",
    }
