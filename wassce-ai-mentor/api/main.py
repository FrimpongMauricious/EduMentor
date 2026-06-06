from contextlib import asynccontextmanager
from fastapi import FastAPI
from db.database import init_db
from api.routes import whatsapp, ussd, health


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Starting WASSCE AI Mentor...")
    init_db()
    print("Database ready.")
    yield
    print("Shutting down.")


app = FastAPI(
    title="WASSCE AI Mentor",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(whatsapp.router)
app.include_router(ussd.router)
