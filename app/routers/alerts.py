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


@router.get("/alerts/stream", summary="SSE-поток новых аномалий из anomaly_alerts")
async def stream_alerts(request: Request):
    async def generator():
        last_dt = (datetime.utcnow() - timedelta(seconds=3600)).strftime("%Y-%m-%d %H:%M:%S")
        while True:
            if await request.is_disconnected():
                break
            try:
                rows = await clickhouse_query(f"""
                    SELECT
                        toString(alert_id),
                        toString(detected_at),
                        anomaly_type,
                        metric_name,
                        severity,
                        details
                    FROM bank_marts.anomaly_alerts
                    WHERE detected_at > '{last_dt}'
                    ORDER BY detected_at ASC
                """)
                for r in rows:
                    last_dt = r[1]
                    yield {
                        "data": json.dumps({
                            "alert_id": r[0],
                            "detected_at": r[1],
                            "anomaly_type": r[2],
                            "metric_name": r[3],
                            "severity": r[4],
                            "details": r[5],
                        }, ensure_ascii=False)
                    }
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
    users = result.scalars().all()

    return AssignResponse(alert=alert, users=list(users))
