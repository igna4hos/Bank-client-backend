import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(settings.postgres_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def _ch_raw(query: str) -> dict:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"http://{settings.clickhouse_host}:{settings.clickhouse_port}/",
            content=query,
            params={
                "user": settings.clickhouse_user,
                "password": settings.clickhouse_password,
                "database": settings.clickhouse_db,
                "default_format": "JSONCompact",
            },
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()


async def clickhouse_query(query: str) -> list[list]:
    return (await _ch_raw(query))["data"]


async def clickhouse_query_with_meta(query: str) -> dict:
    raw = await _ch_raw(query)
    return {
        "columns": [col["name"] for col in raw.get("meta", [])],
        "rows": raw["data"],
    }
