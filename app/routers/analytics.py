import re
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.database import clickhouse_query
from app.schemas import DailyFrictionResponse, FunnelListResponse, FunnelStats

router = APIRouter(prefix="/analytics", tags=["analytics"])

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


@router.get("/funnels", response_model=FunnelListResponse, summary="Список всех воронок")
async def get_funnels():
    rows = await clickhouse_query(
        "SELECT DISTINCT toString(funnel_id) FROM bank_marts.daily_friction_stats ORDER BY 1"
    )
    return FunnelListResponse(funnels=[r[0] for r in rows])


@router.get(
    "/daily-friction",
    response_model=DailyFrictionResponse,
    summary="Статистика avg_task_duration_sec за сегодня по всем воронкам или конкретной",
)
async def get_daily_friction(funnel_id: Optional[str] = Query(None, description="UUID воронки")):
    if funnel_id and not _UUID_RE.match(funnel_id):
        raise HTTPException(status_code=400, detail="Invalid funnel_id format (expected UUID)")

    filter_clause = f"AND toString(funnel_id) = '{funnel_id}'" if funnel_id else ""

    rows = await clickhouse_query(f"""
        SELECT
            toString(funnel_id),
            min(avg_task_duration_sec),
            round(avg(avg_task_duration_sec), 2),
            round(median(avg_task_duration_sec), 2),
            max(avg_task_duration_sec)
        FROM bank_marts.daily_friction_stats
        WHERE date = today() {filter_clause}
        GROUP BY funnel_id
        ORDER BY funnel_id
    """)

    stats = [
        FunnelStats(
            funnel_id=r[0],
            min_duration_sec=round(r[1], 2),
            avg_duration_sec=round(r[2], 2),
            median_duration_sec=round(r[3], 2),
            max_duration_sec=round(r[4], 2),
        )
        for r in rows
    ]
    return DailyFrictionResponse(date=date.today().isoformat(), stats=stats)
