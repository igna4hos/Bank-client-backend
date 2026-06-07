from typing import Optional

from pydantic import BaseModel


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


class FunnelListResponse(BaseModel):
    funnels: list[str]


class FunnelStats(BaseModel):
    funnel_id: str
    min_duration_sec: float
    avg_duration_sec: float
    median_duration_sec: float
    max_duration_sec: float


class DailyFrictionResponse(BaseModel):
    date: str
    stats: list[FunnelStats]
