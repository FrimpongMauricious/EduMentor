from contextlib import asynccontextmanager
from fastapi import FastAPI
from api.routes import whatsapp, ussd, health


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("WASSCE AI Mentor is running")
    yield


app = FastAPI(
    title="WASSCE AI Mentor",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(whatsapp.router)
app.include_router(ussd.router)
