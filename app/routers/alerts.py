import asyncio
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.database import clickhouse_query, get_db
from app.models import User
from app.schemas import AlertInfo, AssignRequest, AssignResponse

router = APIRouter(prefix="/analytics", tags=["alerts"])

_POLL_SECONDS = 5


def _to_moscow(dt_str: str) -> str:
    try:
        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        return (dt + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return dt_str


async def _get_count() -> int:
    rows = await clickhouse_query("SELECT count() FROM bank_marts.anomaly_alerts")
    return int(rows[0][0]) if rows else 0


async def _fetch_alert(alert_id: str) -> AlertInfo | None:
    rows = await clickhouse_query(f"""
        SELECT
            toString(alert_id),
            toString(detected_at),
            anomaly_type,
            metric_name,
            severity,
            details
        FROM bank_marts.anomaly_alerts
        WHERE toString(alert_id) = '{alert_id}'
        LIMIT 1
    """)
    if not rows:
        return None
    r = rows[0]
    return AlertInfo(
        alert_id=r[0], detected_at=r[1], anomaly_type=r[2],
        metric_name=r[3], severity=r[4], details=r[5],
    )


@router.get("/alerts/stream", summary="SSE-поток новых аномалий (отслеживание по изменению количества строк)")
async def stream_alerts(request: Request):
    async def generator():
        last_count = await _get_count()

        while True:
            if await request.is_disconnected():
                break
            try:
                current_count = await _get_count()
                if current_count > last_count:
                    new_n = current_count - last_count
                    rows = await clickhouse_query(f"""
                        SELECT
                            toString(alert_id),
                            toString(detected_at),
                            anomaly_type,
                            metric_name,
                            severity,
                            details
                        FROM bank_marts.anomaly_alerts
                        ORDER BY detected_at DESC
                        LIMIT {new_n}
                    """)
                    for r in rows:
                        yield {
                            "data": json.dumps({
                                "alert_id": r[0],
                                "detected_at_msk": _to_moscow(r[1]),
                                "anomaly_type": r[2],
                                "metric_name": r[3],
                                "severity": r[4],
                                "details": r[5],
                            }, ensure_ascii=False)
                        }
                    last_count = current_count
            except Exception:
                pass
            await asyncio.sleep(_POLL_SECONDS)

    return EventSourceResponse(generator())


@router.post("/alerts/assign", response_model=AssignResponse, summary="Назначить аномалию на департамент")
async def assign_alert(payload: AssignRequest, db: AsyncSession = Depends(get_db)):
    alert = await _fetch_alert(payload.alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    result = await db.execute(
        select(User).where(func.lower(User.department) == payload.department.lower())
    )
    return AssignResponse(alert=alert, users=list(result.scalars().all()))
