"""Схемы админских эндпоинтов утверждённых inbound'ов панели 3x-UI."""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class AdminPanelInbound(BaseModel):
    inbound_id: int
    remark: Optional[str] = None
    protocol: Optional[str] = None
    enable: bool = True
    approved: bool = False


class AdminInboundsResponse(BaseModel):
    inbounds: List[AdminPanelInbound]
    approved_ids: List[int]
    # Утверждённые id, которых больше нет в панели (недоступны для sync).
    missing_approved_ids: List[int]


class AdminApprovedInboundItem(BaseModel):
    inbound_id: int
    sort_order: int
    created_at: Optional[datetime] = None
    created_by_tg_id: Optional[int] = None


class AdminApprovedInboundsResponse(BaseModel):
    approved_inbound_ids: List[int]
    items: List[AdminApprovedInboundItem]


class AdminInboundApproveRequest(BaseModel):
    # После одобрения сразу запустить синхронизацию всех клиентов.
    sync: bool = True


class AdminInboundApproveResponse(BaseModel):
    inbound_id: int
    approved_inbound_ids: List[int]
    created: bool
    job_id: Optional[str] = None


class AdminInboundRevokeResponse(BaseModel):
    inbound_id: int
    approved_inbound_ids: List[int]
    job_id: Optional[str] = None


class AdminInboundSyncRequest(BaseModel):
    # None — весь утверждённый список.
    inbound_ids: Optional[List[int]] = None
    # Защита от гонки: если текущее число ключей в БД отличается — 409.
    expected_total: Optional[int] = Field(default=None, ge=0)


class AdminInboundSyncPreviewResponse(BaseModel):
    target_inbound_ids: List[int]
    approved_inbound_ids: List[int]
    added_inbound_ids: List[int]
    removed_inbound_ids: List[int]
    total_clients: int


class AdminInboundSyncStartedResponse(BaseModel):
    job_id: str
    status: str
    target_inbound_ids: List[int]
    total_clients: int


class AdminInboundSyncErrorItem(BaseModel):
    email: str
    error: str


class AdminInboundSyncStatusResponse(BaseModel):
    job_id: str
    status: str
    target_inbound_ids: List[int]
    total_clients: int
    processed: int
    success: int
    failed: int
    cancelled: bool
    started_at: datetime
    finished_at: Optional[datetime] = None
    errors: List[AdminInboundSyncErrorItem]
