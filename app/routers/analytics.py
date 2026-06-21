import asyncio
import re
import statistics
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from rapidfuzz import fuzz, process

from app.database import clickhouse_query, clickhouse_query_with_meta
from app.schemas import (
    DailyFrictionResponse,
    DayFrictionStats,
    FunnelInfo,
    FunnelListResponse,
    QueryRequest,
    QueryResponse,
    ServiceInfo,
    ServiceListResponse,
    ServiceUsageDayData,
    ServiceUsageResponse,
)

_SELECT_RE = re.compile(r"^\s*SELECT\b", re.IGNORECASE)
_DANGEROUS_RE = re.compile(r"\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|SYSTEM)\b", re.IGNORECASE)

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


@router.post(
    "/query",
    response_model=QueryResponse,
    summary="Выполнить произвольный SELECT-запрос",
)
async def execute_query(payload: QueryRequest):
    sql = payload.sql.strip()
    if not _SELECT_RE.match(sql):
        raise HTTPException(status_code=400, detail="Разрешены только SELECT-запросы")
    if _DANGEROUS_RE.search(sql):
        raise HTTPException(status_code=400, detail="Запрос содержит запрещённые операторы")
    try:
        result = await clickhouse_query_with_meta(sql)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))
    result["rows"] = result["rows"][:100]
    return QueryResponse(**result)


@router.get("/businesses-by-industry", summary="Количество бизнесов и оборот по отраслям")
async def get_businesses_by_industry():
    counts, turnover = await asyncio.gather(
        clickhouse_query("""
            SELECT industry, count() AS cnt
            FROM bank_marts.dim_businesses
            GROUP BY industry
            ORDER BY cnt DESC
        """),
        clickhouse_query("""
            SELECT db.industry, round(sum(dt.inflow_sum), 2) AS total_inflow
            FROM bank_marts.daily_turnover AS dt
            JOIN bank_marts.dim_businesses AS db ON dt.business_id = db.business_id
            GROUP BY db.industry
            ORDER BY total_inflow DESC
        """),
    )
    return {
        "industry_counts": [{"industry": r[0], "count": int(r[1])} for r in counts],
        "industry_turnover": [{"industry": r[0], "total_inflow": float(r[1])} for r in turnover],
    }


@router.get("/alerts/daily-summary", summary="Сводка аномалий за вчера (для утреннего отчёта)")
async def get_alerts_daily_summary():
    rows = await clickhouse_query("""
        SELECT anomaly_type, severity, count() AS cnt
        FROM bank_marts.anomaly_alerts
        WHERE toDate(detected_at) = yesterday()
        GROUP BY anomaly_type, severity
        ORDER BY anomaly_type, severity
    """)
    by_type: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    total = 0
    for r in rows:
        atype, sev, cnt = r[0], r[1], int(r[2])
        by_type[atype] = by_type.get(atype, 0) + cnt
        by_severity[sev] = by_severity.get(sev, 0) + cnt
        total += cnt
    return {
        "date": "yesterday",
        "total": total,
        "by_type": [{"anomaly_type": k, "count": v} for k, v in sorted(by_type.items())],
        "by_severity": [{"severity": k, "count": v} for k, v in sorted(by_severity.items())],
    }
