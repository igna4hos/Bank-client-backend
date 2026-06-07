from fastapi import APIRouter
from sqlalchemy import text

from app.database import AsyncSessionLocal, clickhouse_query

router = APIRouter(tags=["health"])


@router.get("/health", summary="Проверка работоспособности сервиса")
async def health():
    checks: dict[str, str] = {}

    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as e:
        checks["postgres"] = str(e)

    try:
        await clickhouse_query("SELECT 1")
        checks["clickhouse"] = "ok"
    except Exception as e:
        checks["clickhouse"] = str(e)

    status = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return {"status": status, "checks": checks}
