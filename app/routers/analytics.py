import statistics
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from rapidfuzz import fuzz, process

from app.database import clickhouse_query
from app.schemas import (
    DailyFrictionResponse,
    DayFrictionStats,
    FunnelInfo,
    FunnelListResponse,
    ServiceInfo,
    ServiceListResponse,
    ServiceUsageDayData,
    ServiceUsageResponse,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _fetch_funnels() -> list[FunnelInfo]:
    rows = await clickhouse_query("""
        SELECT
            toString(f.funnel_id),
            f.funnel_name,
            toString(f.service_id),
            s.service_name,
            f.benchmark_duration_sec
        FROM bank_marts.dim_funnels AS f
        LEFT JOIN bank_marts.dim_services AS s ON f.service_id = s.service_id
        WHERE f.is_active = 1
        ORDER BY f.funnel_name
    """)
    return [
        FunnelInfo(
            funnel_id=r[0],
            funnel_name=r[1],
            service_id=r[2],
            service_name=r[3] or "",
            benchmark_duration_sec=float(r[4]),
        )
        for r in rows
    ]


def _best_match(query: str, items: list, key: str) -> Optional[dict]:
    names = [item[key] if isinstance(item, dict) else getattr(item, key) for item in items]
    hit = process.extractOne(query, names, scorer=fuzz.WRatio, score_cutoff=45)
    if hit:
        return items[hit[2]]
    return None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/funnels", response_model=FunnelListResponse, summary="Список воронок с привязкой к сервису")
async def get_funnels():
    return FunnelListResponse(funnels=await _fetch_funnels())


@router.get(
    "/daily-friction",
    response_model=DailyFrictionResponse,
    summary="Статистика времени по воронке за сегодня и вчера (поиск по названию)",
)
async def get_daily_friction(
    funnel_name: str = Query(..., description="Название воронки (допускаются опечатки)"),
):
    funnels = await _fetch_funnels()
    funnel = _best_match(funnel_name, funnels, "funnel_name")
    if not funnel:
        raise HTTPException(status_code=404, detail=f"Воронка '{funnel_name}' не найдена")

    rows = await clickhouse_query(f"""
        SELECT
            date,
            min(avg_task_duration_sec),
            round(avg(avg_task_duration_sec), 2),
            round(median(avg_task_duration_sec), 2),
            max(avg_task_duration_sec)
        FROM bank_marts.daily_friction_stats
        WHERE date IN (today(), yesterday())
          AND toString(funnel_id) = '{funnel.funnel_id}'
        GROUP BY date
        ORDER BY date DESC
    """)

    return DailyFrictionResponse(
        funnel_id=funnel.funnel_id,
        funnel_name=funnel.funnel_name,
        service_name=funnel.service_name,
        benchmark_duration_sec=funnel.benchmark_duration_sec,
        stats=[
            DayFrictionStats(
                date=str(r[0]),
                min_duration_sec=round(float(r[1]), 2),
                avg_duration_sec=round(float(r[2]), 2),
                median_duration_sec=round(float(r[3]), 2),
                max_duration_sec=round(float(r[4]), 2),
            )
            for r in rows
        ],
    )


@router.get("/services", response_model=ServiceListResponse, summary="Список сервисов")
async def get_services():
    rows = await clickhouse_query("""
        SELECT toString(service_id), service_name, service_type
        FROM bank_marts.dim_services
        WHERE is_active = 1
        ORDER BY service_name
    """)
    return ServiceListResponse(
        services=[ServiceInfo(service_id=r[0], service_name=r[1], service_type=r[2]) for r in rows]
    )


@router.get("/service-usage", response_model=ServiceUsageResponse, summary="Статистика использования сервиса по дням")
async def get_service_usage(
    service_name: str = Query(..., description="Название сервиса"),
    days: int = Query(10, ge=1, le=14, description="Количество дней (1-14, по умолчанию 10)"),
):
    svc_rows = await clickhouse_query(
        "SELECT toString(service_id), service_name FROM bank_marts.dim_services WHERE is_active = 1"
    )
    services = [{"service_id": r[0], "service_name": r[1]} for r in svc_rows]
    service = _best_match(service_name, services, "service_name")
    if not service:
        raise HTTPException(status_code=404, detail=f"Сервис '{service_name}' не найден")

    rows = await clickhouse_query(f"""
        SELECT date, sum(session_count)
        FROM bank_marts.daily_service_usage
        WHERE toString(service_id) = '{service["service_id"]}'
          AND date >= yesterday() - {days - 1}
          AND date <= yesterday()
        GROUP BY date
        ORDER BY date ASC
    """)

    counts = [int(r[1]) for r in rows]
    median_val = round(statistics.median(counts), 1) if counts else 0.0

    return ServiceUsageResponse(
        service_name=service["service_name"],
        days=days,
        data=[ServiceUsageDayData(date=str(r[0]), session_count=int(r[1])) for r in rows],
        median_sessions=median_val,
    )
