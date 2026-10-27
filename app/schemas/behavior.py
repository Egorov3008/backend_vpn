"""Ответы отчёта «Поведение» и ленты событий пользователя (веб-админка)."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class BehaviorFunnel(BaseModel):
    registered: int
    got_key: int
    connected: int
    paid: int


class BehaviorCohort(BaseModel):
    month: str
    size: int
    # retained[i] — сколько из когорты ещё с ключом к началу месяца month + i + 1.
    retained: List[int]


class BehaviorRiskKey(BaseModel):
    email: str
    tg_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    expiry_time: int
    used_traffic: float
    tariff: Optional[str] = None


class BehaviorChurn(BaseModel):
    left_users: int
    active_paying: int
    renewals: int
    not_connected: int
    risk_days: int
    at_risk: List[BehaviorRiskKey]


class BehaviorAbandoned(BaseModel):
    created: int
    count: int
    users: int
    amount: float


class BehaviorDay(BaseModel):
    day: str
    miniapp: int
    web: int


class BehaviorEventCount(BaseModel):
    name: str
    count: int
    users: int


class BehaviorActivity(BaseModel):
    since: Optional[datetime] = None
    users: int
    miniapp_users: int
    web_users: int
    daily: List[BehaviorDay]
    events: List[BehaviorEventCount]


class AdminBehaviorResponse(BaseModel):
    tz: str
    days: int
    start: datetime
    end: datetime
    funnel: BehaviorFunnel
    cohorts: List[BehaviorCohort]
    churn: BehaviorChurn
    abandoned: BehaviorAbandoned
    activity: BehaviorActivity
    # Русские подписи событий из каталога: name -> title.
    event_titles: Dict[str, str]


class AdminUserEvent(BaseModel):
    name: str
    title: str
    source: str
    props: Dict[str, Any]
    created_at: datetime
