from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database import Base, engine
from app.routers import alerts, analytics, ask, health, users


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(
    title="Bank Client Backend",
    description="API для Telegram-бота банковской платформы",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(users.router)
app.include_router(analytics.router)
app.include_router(alerts.router)
app.include_router(ask.router)
