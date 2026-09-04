"""
api/main.py — FastAPI application entry point.
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from db.database import init_db
from api.routes import whatsapp, ussd, health, dashboard
from api.reminders import router as reminders_router
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

# The React dashboard is a separate origin/service, so it needs CORS.
# "*" is fine for a student project pilot; restrict to the deployed React
# origin (e.g. https://wassce-ai-mentor-react.onrender.com) in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(whatsapp.router)
app.include_router(ussd.router)
app.include_router(reminders_router)
app.include_router(dashboard.router)


@app.get("/", tags=["root"])
async def root():
    return {
        "service": "WASSCE AI Mentor",
        "status": "online",
        "docs": "/docs",
        "health": "/health",
    }
