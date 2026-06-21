from typing import Any, Optional

from pydantic import BaseModel


# ── Users ─────────────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    telegram_nick: str
    chat_id: int
    department: str
    role: str = "manager"


class UserResponse(BaseModel):
    id: int
    telegram_nick: str
    chat_id: int
    role: str
    department: Optional[str] = None

    model_config = {"from_attributes": True}


# ── Funnels ───────────────────────────────────────────────────────────────────

class FunnelInfo(BaseModel):
    funnel_id: str
    funnel_name: str
    service_id: str
    service_name: str
    benchmark_duration_sec: float


class FunnelListResponse(BaseModel):
    funnels: list[FunnelInfo]


# ── Daily friction ─────────────────────────────────────────────────────────────

class DayFrictionStats(BaseModel):
    date: str
    min_duration_sec: float
    avg_duration_sec: float
    median_duration_sec: float
    max_duration_sec: float


class DailyFrictionResponse(BaseModel):
    funnel_id: str
    funnel_name: str
    service_name: str
    benchmark_duration_sec: float
    stats: list[DayFrictionStats]


# ── Services ──────────────────────────────────────────────────────────────────

class ServiceInfo(BaseModel):
    service_id: str
    service_name: str
    service_type: str


class ServiceListResponse(BaseModel):
    services: list[ServiceInfo]


# ── Service usage ─────────────────────────────────────────────────────────────

class ServiceUsageDayData(BaseModel):
    date: str
    session_count: int


class ServiceUsageResponse(BaseModel):
    service_name: str
    days: int
    data: list[ServiceUsageDayData]
    median_sessions: float


# ── Alerts ────────────────────────────────────────────────────────────────────

class AlertInfo(BaseModel):
    alert_id: str
    detected_at: str
    anomaly_type: str
    metric_name: str
    severity: str
    details: str


class AssignRequest(BaseModel):
    alert_id: str
    department: str


class AssignResponse(BaseModel):
    alert: AlertInfo
    users: list[UserResponse]


# ── Ad-hoc query ──────────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    sql: str


class QueryResponse(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
