"""Утверждённые inbound'ы панели 3x-UI (/api/v1/admin/inbounds, /admin/inbound-sync).

Чтение (`router`, ключ бота или сессия админа) показывает inbound'ы панели и
утверждённый список. Мутации (`destructive_router`, только админ + аудит):
approve/revoke меняют БД и кеш (после первого одобрения БД — источник истины,
`.env` больше не применяется), а sync-задачи в фоне приводят inbound-набор всех
клиентов БД к утверждённому списку.
"""
import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.auth import AdminPrincipal, verify_admin_actor, verify_admin_or_bot
from app.dependencies import get_cache, get_pool, get_service_data
from app.factories import (
    build_approved_inbounds_service,
    build_key_services,
    inbound_sync_jobs,
)
from app.schemas.inbounds import (
    AdminApprovedInboundItem,
    AdminApprovedInboundsResponse,
    AdminInboundApproveRequest,
    AdminInboundApproveResponse,
    AdminInboundRevokeResponse,
    AdminInboundSyncPreviewResponse,
    AdminInboundSyncRequest,
    AdminInboundSyncStartedResponse,
    AdminInboundSyncStatusResponse,
    AdminInboundsResponse,
)
from database.service import DataService
from services.admin_audit import AuditLogger
from services.cache.service import CacheService
from services.core.data.service import ServiceDataModel
from services.core.keys.utils.inbounds import approved_inbound_ids
from services.core.panel.approved import (
    InboundNotOnPanel,
    InvalidSyncTarget,
    LastApprovedInbound,
    NoApprovedInbounds,
    PanelUnavailable,
)

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(verify_admin_or_bot)])
destructive_router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(verify_admin_actor)])

# Пустой target: синхронизировать некуда, ключи остались бы без VPN.
_STATUS_BY_ERROR = (
    (PanelUnavailable, 502),
    (InboundNotOnPanel, 404),
    (LastApprovedInbound, 409),
    (NoApprovedInbounds, 409),
    (InvalidSyncTarget, 422),
)


def _http_error(exc: Exception) -> HTTPException:
    for exc_type, status in _STATUS_BY_ERROR:
        if isinstance(exc, exc_type):
            detail = str(exc)
            if isinstance(exc, InvalidSyncTarget):
                detail = {
                    "message": str(exc),
                    "invalid_inbound_ids": exc.invalid_inbound_ids,
                    "approved_inbound_ids": exc.approved_inbound_ids,
                }
            return HTTPException(status_code=status, detail=detail)
    return HTTPException(status_code=500, detail="Unexpected inbound error")


async def _run_sync(
    pool,
    service_data: ServiceDataModel,
    cache: CacheService,
    data_service: DataService,
    target: list[int],
    admin_tg_id,
    *,
    expected_total=None,
) -> tuple:
    """Собрать клиентов БД и запустить job. Возвращает (job_id, running_job_id)."""
    total = await data_service.keys.count_all(pool)
    if expected_total is not None and expected_total != total:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Key count changed",
                "expected_total": expected_total,
                "actual_total": total,
            },
        )
    if total == 0:
        raise HTTPException(status_code=409, detail="No keys to synchronize")

    emails = await data_service.keys.list_all_emails(pool)
    _, _, xui = build_key_services(pool, service_data, cache, data_service)
    job_id, running = inbound_sync_jobs().start(
        emails,
        target,
        admin_tg_id,
        xui=xui,
        pool=pool,
    )
    if job_id is not None:
        await AuditLogger(pool).record(
            admin_tg_id, "inbound_sync_start", f"{job_id}: {sorted(target)}"
        )
    return job_id, running


@router.get("/inbounds", response_model=AdminInboundsResponse)
async def admin_list_inbounds(
    pool: asyncpg.Pool = Depends(get_pool),
    cache: CacheService = Depends(get_cache),
    service_data: ServiceDataModel = Depends(get_service_data),
):
    """Inbound'ы панели 3x-UI с флагом ``approved``; 502, если панель недоступна."""
    service = build_approved_inbounds_service(pool, service_data, cache, DataService())
    try:
        return await service.list_panel(cache)
    except PanelUnavailable as e:
        raise _http_error(e) from e


@router.get("/inbounds/approved", response_model=AdminApprovedInboundsResponse)
async def admin_list_approved_inbounds(
    pool: asyncpg.Pool = Depends(get_pool),
    cache: CacheService = Depends(get_cache),
):
    """Утверждённый список из БД/кеша (первый элемент — primary inbound)."""
    rows = await DataService().approved_inbounds.list_ordered(pool)
    return {
        "approved_inbound_ids": [int(row.inbound_id) for row in rows],
        "items": [
            AdminApprovedInboundItem(
                inbound_id=int(row.inbound_id),
                sort_order=int(row.sort_order or 0),
                created_at=row.created_at,
                created_by_tg_id=row.created_by_tg_id,
            )
            for row in rows
        ],
    }


@destructive_router.post(
    "/inbounds/{inbound_id}/approve", response_model=AdminInboundApproveResponse
)
async def admin_approve_inbound(
    inbound_id: int,
    body: AdminInboundApproveRequest,
    response: Response,
    principal: AdminPrincipal = Depends(verify_admin_actor),
    pool: asyncpg.Pool = Depends(get_pool),
    cache: CacheService = Depends(get_cache),
    service_data: ServiceDataModel = Depends(get_service_data),
):
    """Добавить inbound в утверждённые и (по умолчанию) сразу синхронизировать клиентов.

    Если задача синхронизации уже идёт, одобрение всё равно сохраняется,
    ответ — 409 с ``job_id``: к клиентам изменение применится при следующем запуске.
    """
    data_service = DataService()
    service = build_approved_inbounds_service(pool, service_data, cache, data_service)
    try:
        _, created = await service.approve(
            pool, cache, inbound_id, principal.admin_tg_id
        )
    except (PanelUnavailable, InboundNotOnPanel) as e:
        raise _http_error(e) from e

    await AuditLogger(pool).record(
        principal.admin_tg_id, "approve_inbound", str(inbound_id)
    )
    approved = await approved_inbound_ids(cache)

    job_id = None
    if body.sync:
        job_id, running = await _run_sync(
            pool,
            service_data,
            cache,
            data_service,
            approved,
            principal.admin_tg_id,
        )
        if job_id is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Inbound sync is already running",
                    "job_id": running,
                    "inbound_id": inbound_id,
                    "approved_inbound_ids": approved,
                },
            )
        response.status_code = 202

    return {
        "inbound_id": inbound_id,
        "approved_inbound_ids": approved,
        "created": created,
        "job_id": job_id,
    }


@destructive_router.delete(
    "/inbounds/{inbound_id}", response_model=AdminInboundRevokeResponse
)
async def admin_revoke_inbound(
    inbound_id: int,
    response: Response,
    sync: bool = Query(True, description="Сразу применить новый набор к клиентам"),
    principal: AdminPrincipal = Depends(verify_admin_actor),
    pool: asyncpg.Pool = Depends(get_pool),
    cache: CacheService = Depends(get_cache),
    service_data: ServiceDataModel = Depends(get_service_data),
):
    """Отозвать inbound из утверждённых; последний отозвать нельзя (409)."""
    data_service = DataService()
    service = build_approved_inbounds_service(pool, service_data, cache, data_service)
    try:
        removed = await service.revoke(pool, cache, inbound_id)
    except (PanelUnavailable, LastApprovedInbound) as e:
        raise _http_error(e) from e
    if not removed:
        raise HTTPException(status_code=404, detail="Inbound is not approved")

    await AuditLogger(pool).record(
        principal.admin_tg_id, "revoke_inbound", str(inbound_id)
    )
    approved = await approved_inbound_ids(cache)

    if not sync:
        # Отзыв без применения: тело не нужно, ответ 204.
        return Response(status_code=204)

    job_id = None
    try:
        target = await service.resolve_target(cache, None)
    except (PanelUnavailable, NoApprovedInbounds, InvalidSyncTarget) as e:
        # Отзыв уже сохранён; к клиентам он не применён — нужен ручной sync.
        raise _http_error(e) from e
    job_id, running = await _run_sync(
        pool,
        service_data,
        cache,
        data_service,
        target,
        principal.admin_tg_id,
    )
    if job_id is None:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Inbound sync is already running",
                "job_id": running,
                "approved_inbound_ids": approved,
            },
        )
    response.status_code = 202

    return {
        "inbound_id": inbound_id,
        "approved_inbound_ids": approved,
        "job_id": job_id,
    }


@destructive_router.post(
    "/inbound-sync/preview", response_model=AdminInboundSyncPreviewResponse
)
async def admin_preview_inbound_sync(
    body: AdminInboundSyncRequest,
    pool: asyncpg.Pool = Depends(get_pool),
    cache: CacheService = Depends(get_cache),
    service_data: ServiceDataModel = Depends(get_service_data),
):
    """Проверить target и показать объём предстоящего обновления (ничего не меняет)."""
    data_service = DataService()
    service = build_approved_inbounds_service(pool, service_data, cache, data_service)
    try:
        target = await service.resolve_target(cache, body.inbound_ids)
    except (PanelUnavailable, NoApprovedInbounds, InvalidSyncTarget) as e:
        raise _http_error(e) from e

    approved = await approved_inbound_ids(cache)
    total = await data_service.keys.count_all(pool)
    target_set = set(target)
    return {
        "target_inbound_ids": target,
        "approved_inbound_ids": approved,
        "added_inbound_ids": [i for i in target if i not in set(approved)],
        "removed_inbound_ids": [i for i in approved if i not in target_set],
        "total_clients": total,
    }


@destructive_router.post(
    "/inbound-sync",
    response_model=AdminInboundSyncStartedResponse,
    status_code=202,
)
async def admin_start_inbound_sync(
    body: AdminInboundSyncRequest,
    principal: AdminPrincipal = Depends(verify_admin_actor),
    pool: asyncpg.Pool = Depends(get_pool),
    cache: CacheService = Depends(get_cache),
    service_data: ServiceDataModel = Depends(get_service_data),
):
    """Запустить фоновую синхронизацию inbound-набора всех клиентов БД."""
    data_service = DataService()
    service = build_approved_inbounds_service(pool, service_data, cache, data_service)
    try:
        target = await service.resolve_target(cache, body.inbound_ids)
    except (PanelUnavailable, NoApprovedInbounds, InvalidSyncTarget) as e:
        raise _http_error(e) from e

    job_id, running = await _run_sync(
        pool,
        service_data,
        cache,
        data_service,
        target,
        principal.admin_tg_id,
        expected_total=body.expected_total,
    )
    if job_id is None:
        raise HTTPException(
            status_code=409,
            detail={"message": "Inbound sync is already running", "job_id": running},
        )
    state = await inbound_sync_jobs().get(job_id)
    return {
        "job_id": job_id,
        "status": state.status,
        "target_inbound_ids": target,
        "total_clients": state.total_clients,
    }


@destructive_router.get(
    "/inbound-sync/{job_id}", response_model=AdminInboundSyncStatusResponse
)
async def admin_inbound_sync_status(
    job_id: str,
    principal: AdminPrincipal = Depends(verify_admin_actor),
):
    """Прогресс и ошибки задачи синхронизации."""
    state = await inbound_sync_jobs().get(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return state.as_dict()


@destructive_router.post(
    "/inbound-sync/{job_id}/cancel", response_model=AdminInboundSyncStatusResponse
)
async def admin_cancel_inbound_sync(
    job_id: str,
    principal: AdminPrincipal = Depends(verify_admin_actor),
    pool: asyncpg.Pool = Depends(get_pool),
):
    """Кооперативная остановка: in-flight клиент может успеть обновиться."""
    state = await inbound_sync_jobs().cancel(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if state.status != "running":
        raise HTTPException(
            status_code=409,
            detail={"message": "Job already finished", "status": state.status},
        )
    await AuditLogger(pool).record(
        principal.admin_tg_id, "inbound_sync_cancel", job_id
    )
    return state.as_dict()
