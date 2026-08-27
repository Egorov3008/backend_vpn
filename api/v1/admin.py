from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
import asyncpg

from app.auth import verify_admin_or_bot, verify_admin_actor, AdminPrincipal
from app.dependencies import get_service_data, get_pool, get_cache
from app.factories import build_key_services
from services.admin_audit import AuditLogger
from app.schemas.users import UserResponse, UserUpdateRequest, UserRegisterRequest
from app.schemas.admin import (
    AdminGenerateKeyRequest,
    AdminMassRenewRequest,
    AdminChangeDateRequest,
    AdminChangeTariffRequest,
    AdminMaintenanceModeRequest,
    AdminUpdatePanelMetaRequest,
    AdminStatsResponse,
    AdminDashboardMetricsResponse,
    AdminGraceBonusStatsResponse,
    AdminSchedulerStatusResponse,
    MaintenanceStatusResponse,
    AdminUserStockResponse,
    AdminInactiveUsersResponse,
    AdminDeleteCountResponse,
    AdminGenerateKeyResponse,
    AdminMassRenewResponse,
    AdminChangeDateResponse,
    AdminChangeTariffResponse,
    AdminPanelMetaResponse,
    AdminGiftResponse,
    AdminGiftsListResponse,
    AdminTariffDetailResponse,
    AdminTariffItem,
    AdminTariffsListResponse,
    AdminReferralLinkResponse,
    AdminReferralLinkDetailResponse,
    AdminReferralStatsResponse,
    AdminKeyItem,
    AdminKeysListResponse,
    AdminPaymentItem,
    AdminPaymentsListResponse,
    AdminDeleteUserResponse,
    AdminSyncStartedResponse,
    AdminSyncStatusResponse,
)
from models.stocks.stock import Stock
from database.service import DataService
from logger import logger
from models import User
from services.api_clients.service import ApiClientService
from services.cache.key_manager import CacheKeyManager
from services.cache.service import CacheService
from services.core.data.service import ServiceDataModel
from services.core.keys.admin_report import KeyAdminReport
from services.core.keys.utils.reset import KeyResetter
from services.core.keys.utils.inbounds import paid_inbound_ids
from services.system.maintenance import maintenance_mode
from app.schemas.api_clients import (
    ApiClientCreateRequest,
    ApiClientCreatedResponse,
    ApiClientResponse,
    ApiClientsListResponse,
)

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(verify_admin_or_bot)],
)

# Деструктивные admin-операции: actor-auth структурно на router-уровне, а не
# per-endpoint. Любой эндпоинт, добавленный в destructive_router, автоматически
# получает verify_admin_actor (X-API-Key + X-Admin-Tg-Id) — нельзя случайно
# забыть auth при добавлении нового деструктивного метода. Per-endpoint
# `principal: AdminPrincipal = Depends(verify_admin_actor)` сохранён как
# источник principal.admin_tg_id для audit (router-level dep кешируется
# per-request, повторный Depends возвращает тот же объект без повторной проверки).
destructive_router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(verify_admin_actor)],
)


@router.get("/stats", response_model=AdminStatsResponse)
async def get_stats(
    service_data: ServiceDataModel = Depends(get_service_data),
):
    keys = await service_data.keys.get_all()
    users = await service_data.users.get_all()
    stats = await KeyAdminReport().get_summary_stats(keys)
    return {"total_users": len(users), **stats}


@router.get("/dashboard-metrics", response_model=AdminDashboardMetricsResponse)
async def get_dashboard_metrics(
    pool: asyncpg.Pool = Depends(get_pool),
):
    """MRR, воронка, истекающие ключи, статусы платежей — для web-дашборда.

    Раньше web считал это прямым SQL к общей Postgres; теперь backend —
    единственный владелец БД, web получает те же данные отсюда.
    """
    from services.admin_dashboard_metrics import DashboardMetricsService
    return await DashboardMetricsService(pool).get_all_dashboard_metrics()


@router.get("/grace-bonus-stats", response_model=AdminGraceBonusStatsResponse)
async def get_grace_bonus_stats(
    pool: asyncpg.Pool = Depends(get_pool),
):
    """Метрики grace-периода и канального бонуса (cumulative + today/yesterday)."""
    from services.grace_bonus_stats import GraceBonusStatsService
    return await GraceBonusStatsService(pool).get()


@router.get("/scheduler/status", response_model=AdminSchedulerStatusResponse)
async def admin_scheduler_status(
    service_data: ServiceDataModel = Depends(get_service_data),
):
    """Состояние scheduler и сегменты ключей (для админ-панели)."""
    from services.notifications.segmentation import KeySegmenter, KeySegment
    users = await service_data.users.get_all()
    keys = await service_data.keys.get_all()
    seg = KeySegmenter()
    seg_counts = {
        "EXPIRING_10H": sum(1 for k in keys if seg.filter_keys([k], KeySegment.EXPIRING_10H)),
        "EXPIRING_24H": sum(1 for k in keys if seg.filter_keys([k], KeySegment.EXPIRING_24H)),
        "EXPIRED":      sum(1 for k in keys if seg.filter_keys([k], KeySegment.EXPIRED)),
        "TRIAL":        sum(1 for k in keys if seg.filter_keys([k], KeySegment.TRIAL)),
    }
    return {
        "container_alive": True,
        "users": len(users),
        "blocked": sum(1 for u in users if u.is_blocked),
        "keys": len(keys),
        "segment_counts": seg_counts,
    }


@router.get("/maintenance-mode", response_model=MaintenanceStatusResponse)
async def get_maintenance_mode(
    pool: asyncpg.Pool = Depends(get_pool),
):
    """Статус режима профилактики панели 3x-ui (доступен боту по X-Bot-Secret)."""
    return await maintenance_mode.get_status(pool)


@destructive_router.post("/maintenance-mode", response_model=MaintenanceStatusResponse)
async def set_maintenance_mode(
    body: AdminMaintenanceModeRequest,
    principal: AdminPrincipal = Depends(verify_admin_actor),
    pool: asyncpg.Pool = Depends(get_pool),
):
    """Включить/выключить режим профилактики панели 3x-ui.

    Пока включён — продление ключей и оплата новых блокируются (503) на
    уровне CreateKey.proces/KeyRenewal.extension_key.
    """
    status = await maintenance_mode.set(
        pool, enabled=body.enabled, reason=body.reason, admin_tg_id=principal.admin_tg_id
    )
    await AuditLogger(pool).record(
        principal.admin_tg_id,
        "maintenance_mode",
        "enabled" if body.enabled else "disabled",
    )
    return status


@router.get("/users", response_model=List[UserResponse])
async def list_users(
    response: Response,
    limit: Optional[int] = Query(None, ge=1, le=1000, description="Page size; omit for full list (legacy behaviour)"),
    offset: int = Query(0, ge=0),
    service_data: ServiceDataModel = Depends(get_service_data),
) -> List[UserResponse]:
    users = await service_data.users.get_all()
    response.headers["X-Total-Count"] = str(len(users))
    if limit is not None:
        users = users[offset:offset + limit]
    return [UserResponse.from_user(u) for u in users]


@router.get("/users/inactive", response_model=AdminInactiveUsersResponse)
async def list_inactive_users(
    service_data: ServiceDataModel = Depends(get_service_data),
    pool=Depends(get_pool),
):
    """Users with is_blocked=True and no keys."""
    users = await service_data.data_service.users.get_all(pool)
    keys = await service_data.data_service.keys.get_all(pool)
    if not isinstance(users, list):
        users = [users] if users else []
    if not isinstance(keys, list):
        keys = [keys] if keys else []

    users_with_keys = {k.tg_id for k in keys}
    inactive = [
        u for u in users
        if u.is_blocked and u.tg_id not in users_with_keys
    ]
    return {"count": len(inactive), "users": [UserResponse.from_user(u) for u in inactive]}


@router.get("/users/{tg_id}", response_model=UserResponse)
async def admin_get_user(
    tg_id: int,
    service_data: ServiceDataModel = Depends(get_service_data),
) -> UserResponse:
    user = await service_data.users.get_data(tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return UserResponse.from_user(user)


@router.get("/users/{tg_id}/stock", response_model=AdminUserStockResponse)
async def admin_get_user_stock(
    tg_id: int,
    service_data: ServiceDataModel = Depends(get_service_data),
):
    """Get active stock (discount) for a user."""
    stock = await service_data.stocks.get_data(tg_id)
    if not stock or not stock.is_valid:
        return {"has_discount": False, "stock_type": "", "value": 0.0}
    return {
        "has_discount": True,
        "stock_type": stock.stock_type,
        "value": float(stock.value),
        "is_active": stock.is_active,
        "valid_until": stock.valid_until.isoformat() if stock.valid_until else None,
    }


@router.post("/users/register", response_model=UserResponse, status_code=201)
async def admin_register_user(
    body: UserRegisterRequest,
    pool=Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
    cache: CacheService = Depends(get_cache),
) -> UserResponse:
    """Register a new user (called by bot)."""
    from services.core.user.utils.saver import SeverUser
    saver = SeverUser(service_data)
    new_user = await saver.register_user(
        pool,
        tg_id=body.tg_id,
        username=body.username,
        first_name=body.first_name,
        last_name=body.last_name,
        language_code=body.language_code,
        server_id=body.server_id,
        referral_id=body.referral_id,
    )
    await cache.users.set(CacheKeyManager.user(body.tg_id), new_user)

    if body.referral_link_id:
        from models.referrals.referral_redemption import ReferralRedemption
        redemption = ReferralRedemption(
            referral_link_id=body.referral_link_id,
            referred_tg_id=body.tg_id,
        )
        await service_data.data_service.referral_redemptions.create(pool, **redemption.to_dict())
        logger.info(
            "Реферальная привязка создана",
            referrer_tg_id=body.referral_id,
            referred_tg_id=body.tg_id,
        )

    return UserResponse.from_user(new_user)


@destructive_router.patch("/users/{tg_id}", response_model=UserResponse)
async def admin_update_user(
    tg_id: int,
    body: UserUpdateRequest,
    principal: AdminPrincipal = Depends(verify_admin_actor),
    service_data: ServiceDataModel = Depends(get_service_data),
    pool=Depends(get_pool),
) -> UserResponse:
    user = await service_data.users.get_data(tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if body.balance is not None:
        user.balance = body.balance
    if body.server_id is not None:
        user.server_id = body.server_id
    if body.trial is not None:
        user.trial = body.trial
    if body.is_blocked is not None:
        user.is_blocked = body.is_blocked
    if body.is_admin is not None:
        user.is_admin = body.is_admin

    await service_data.users.update(pool, user, search_data={"tg_id": tg_id})
    await AuditLogger(pool).record(principal.admin_tg_id, "update_user", str(tg_id))
    return UserResponse.from_user(user)


@destructive_router.post("/keys/{email}/delete", status_code=204)
async def admin_delete_key(
    email: str,
    principal: AdminPrincipal = Depends(verify_admin_actor),
    pool=Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
    cache: CacheService = Depends(get_cache),
):
    """Admin: delete any VPN key (no tg_id ownership check).

    409 если панель не удалила — ключ остаётся в БД (orphan не возникает).
    """
    key = await service_data.keys.get_data(email)
    if not key:
        key = await service_data.data_service.keys.get(pool, email=email)
        if key:
            await service_data.cache_service.keys.set(CacheKeyManager.key(email), key)
    if not key:
        raise HTTPException(status_code=404, detail="Key not found")

    data_service = DataService()
    _, _, xui = build_key_services(pool, service_data, cache, data_service)

    try:
        deleted = await xui.delete_client(email, key.inbound_id, key.client_id)
    except Exception as e:
        logger.error("Failed to delete key from panel", email=email, error=str(e))
        raise HTTPException(status_code=409, detail="Failed to delete key from panel")

    if not deleted:
        raise HTTPException(status_code=409, detail="Failed to delete key from panel")

    # Аудит фиксируем сразу после успешной XUI-мутации — если последующий
    # DB/cache cleanup упадёт, деструктивная операция на панели уже совершена
    # и должна остаться в журнале (AuditLogger глотает свои ошибки).
    await AuditLogger(pool).record(principal.admin_tg_id, "delete_key", email)
    await service_data.data_service.keys.delete(pool, email=email)
    await service_data.cache_service.keys.delete(CacheKeyManager.key(email))
    return Response(status_code=204)


@destructive_router.post("/users/inactive/delete", response_model=AdminDeleteCountResponse)
async def delete_inactive_users(
    principal: AdminPrincipal = Depends(verify_admin_actor),
    service_data: ServiceDataModel = Depends(get_service_data),
    pool=Depends(get_pool),
    cache: CacheService = Depends(get_cache),
):
    """Delete all inactive users (is_blocked=True, no keys)."""
    users = await service_data.data_service.users.get_all(pool)
    keys = await service_data.data_service.keys.get_all(pool)
    if not isinstance(users, list):
        users = [users] if users else []
    if not isinstance(keys, list):
        keys = [keys] if keys else []

    users_with_keys = {k.tg_id for k in keys}
    inactive = [u for u in users if u.is_blocked and u.tg_id not in users_with_keys]

    deleted = 0
    for user in inactive:
        await service_data.data_service.users.delete(pool, user)
        await service_data.cache_service.users.delete(CacheKeyManager.user(user.tg_id))
        deleted += 1

    await AuditLogger(pool).record(principal.admin_tg_id, "delete_inactive", str(deleted))
    return {"deleted": deleted}


@destructive_router.post("/keys/generate", response_model=AdminGenerateKeyResponse)
async def admin_generate_key(
    body: AdminGenerateKeyRequest,
    principal: AdminPrincipal = Depends(verify_admin_actor),
    pool=Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
    cache: CacheService = Depends(get_cache),
):
    """Admin: generate a key for any user (creates user if missing)."""
    user = await service_data.users.get_data(body.tg_id)
    if not user:
        user = await service_data.data_service.users.get(pool, tg_id=body.tg_id)
        if user:
            await service_data.cache_service.users.set(
                CacheKeyManager.user(body.tg_id), user
            )
    if not user:
        # Create user on-the-fly
        user = User(tg_id=body.tg_id, server_id=body.server_id)
        await service_data.users.save_data(pool, user, tg_id=body.tg_id)

    tariff = await service_data.tariffs.get_data(body.tariff_id, conn=pool)
    if not tariff:
        raise HTTPException(status_code=404, detail="Tariff not found")

    data_service = DataService()
    create_key, _, _ = build_key_services(pool, service_data, cache, data_service)

    result = await create_key.proces(
        tg_id=body.tg_id,
        tariff=tariff,
        server_id=body.server_id,
        conn=pool,
        number_of_months=body.number_of_months,
    )
    if not result:
        raise HTTPException(status_code=500, detail="Failed to create key")
    await AuditLogger(pool).record(
        principal.admin_tg_id, "generate_key", f"{body.tg_id}:{body.tariff_id}"
    )
    return result


@destructive_router.post("/keys/mass-renew", response_model=AdminMassRenewResponse)
async def admin_mass_renew(
    body: AdminMassRenewRequest,
    principal: AdminPrincipal = Depends(verify_admin_actor),
    pool=Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
    cache: CacheService = Depends(get_cache),
):
    """Admin: mass-renew keys by email list."""
    from datetime import datetime, timezone

    data_service = DataService()
    _, _, xui = build_key_services(pool, service_data, cache, data_service)
    resetter = KeyResetter(cache_service=cache)

    results = []
    for email in body.emails:
        key = await service_data.keys.get_data(email)
        if not key:
            key = await service_data.data_service.keys.get(pool, email=email)
        if not key:
            results.append({"email": email, "success": False, "error": "Key not found"})
            continue

        try:
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            old_expiry = key.expiry_time
            base_expiry = max(old_expiry, now_ms)
            new_expiry = base_expiry + (body.days * 24 * 3600 * 1000)

            inbound_ok = await xui.set_inbounds(key.email, paid_inbound_ids())
            if not inbound_ok:
                logger.warning(
                    "Не удалось синхронизировать inbound-набор перед mass-renew; продолжаем best-effort",
                    email=key.email,
                    operation="mass_renew",
                    admin_tg_id=principal.admin_tg_id,
                )
            key.expiry_time = new_expiry
            await xui.extend_client_key(key)

            await service_data.keys.update(pool, key, {"email": key.email})
            await resetter.reset_key_after_renewal(pool, key)

            results.append({"email": email, "success": True, "new_expiry": new_expiry})
        except Exception as e:
            logger.error("Mass renew failed for key", email=email, error=str(e))
            results.append({"email": email, "success": False, "error": str(e)})

    success_count = sum(1 for r in results if r["success"])
    await AuditLogger(pool).record(
        principal.admin_tg_id, "mass_renew", f"{success_count}/{len(body.emails)}"
    )
    return {"total": len(body.emails), "success": success_count, "failed": len(body.emails) - success_count, "results": results}


@destructive_router.post("/keys/{email}/change-date", response_model=AdminChangeDateResponse)
async def admin_change_key_date(
    email: str,
    body: AdminChangeDateRequest,
    principal: AdminPrincipal = Depends(verify_admin_actor),
    pool=Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
    cache: CacheService = Depends(get_cache),
):
    """Admin: change key expiry time."""
    data_service = DataService()
    _, _, xui = build_key_services(pool, service_data, cache, data_service)
    resetter = KeyResetter(cache_service=cache)

    key = await service_data.keys.get_data(email)
    if not key:
        key = await service_data.data_service.keys.get(pool, email=email)
    if not key:
        raise HTTPException(status_code=404, detail="Key not found")

    key.expiry_time = body.expiry_time
    # Синхронизируем inbound-набор с .env перед сменой даты.
    # Best-effort: ошибка set_inbounds логируется и не блокирует операцию.
    inbound_ok = await xui.set_inbounds(key.email, paid_inbound_ids())
    if not inbound_ok:
        logger.warning(
            "Не удалось синхронизировать inbound-набор перед change-date; продолжаем best-effort",
            email=key.email,
            operation="change_date",
            admin_tg_id=principal.admin_tg_id,
        )
    updated = await xui.extend_client_key(key)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update key in panel")
    await service_data.keys.update(pool, key, {"email": key.email})
    # Аудит сразу после XUI+DB-мутации — если resetter упадёт, деструктивная
    # операция на панели уже совершена и должна остаться в журнале
    # (AuditLogger глотает свои ошибки).
    await AuditLogger(pool).record(principal.admin_tg_id, "change_date", email)
    await resetter.reset_key_after_renewal(pool, key)
    return {"email": email, "expiry_time": body.expiry_time}


@destructive_router.post("/keys/{email}/change-tariff", response_model=AdminChangeTariffResponse)
async def admin_change_key_tariff(
    email: str,
    body: AdminChangeTariffRequest,
    principal: AdminPrincipal = Depends(verify_admin_actor),
    pool=Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
    cache: CacheService = Depends(get_cache),
):
    """Admin: change key tariff."""
    data_service = DataService()
    _, _, xui = build_key_services(pool, service_data, cache, data_service)
    resetter = KeyResetter(cache_service=cache)

    key = await service_data.keys.get_data(email)
    if not key:
        key = await service_data.data_service.keys.get(pool, email=email)
    if not key:
        raise HTTPException(status_code=404, detail="Key not found")

    tariff = await service_data.tariffs.get_data(body.tariff_id, conn=pool)
    if not tariff:
        raise HTTPException(status_code=404, detail="Tariff not found")

    key.tariff_id = tariff.id
    key.limit_ip = tariff.limit_ip
    key.name_tariff = tariff.name_tariff

    # Синхронизируем inbound-набор с .env перед сменой тарифа.
    # Best-effort: ошибка set_inbounds логируется и не блокирует операцию.
    inbound_ok = await xui.set_inbounds(key.email, paid_inbound_ids())
    if not inbound_ok:
        logger.warning(
            "Не удалось синхронизировать inbound-набор перед change-tariff; продолжаем best-effort",
            email=key.email,
            operation="change_tariff",
            admin_tg_id=principal.admin_tg_id,
        )

    updated = await xui.extend_client_key(key)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update key in panel")
    await service_data.keys.update(pool, key, {"email": key.email})
    # Аудит сразу после XUI+DB-мутации — если resetter упадёт, деструктивная
    # операция на панели уже совершена и должна остаться в журнале.
    await AuditLogger(pool).record(principal.admin_tg_id, "change_tariff", email)
    await resetter.reset_key_after_renewal(pool, key)
    return {"email": email, "tariff_id": tariff.id}


@destructive_router.post(
    "/keys/{email}/panel-meta",
    response_model=AdminPanelMetaResponse,
    response_model_exclude_none=True,
)
async def admin_update_key_panel_meta(
    email: str,
    body: AdminUpdatePanelMetaRequest,
    principal: AdminPrincipal = Depends(verify_admin_actor),
    pool=Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
    cache: CacheService = Depends(get_cache),
):
    """Admin: change panel-only client metadata (group, comment).

    Эти поля существуют только в 3x-ui (не хранятся в БД/кэше платформы),
    поэтому эндпоинт мутирует исключительно панель.
    """
    overrides = body.model_dump(exclude_none=True)
    if not overrides:
        raise HTTPException(
            status_code=400, detail="At least one of group/comment must be provided"
        )

    data_service = DataService()
    _, _, xui = build_key_services(pool, service_data, cache, data_service)

    key = await service_data.keys.get_data(email)
    if not key:
        key = await service_data.data_service.keys.get(pool, email=email)
    if not key:
        raise HTTPException(status_code=404, detail="Key not found")

    try:
        await xui.update_standalone_client(email, **overrides)
    except Exception as e:
        logger.error(
            "Не удалось обновить group/comment клиента в панели",
            email=email,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail="Failed to update key in panel")

    await AuditLogger(pool).record(principal.admin_tg_id, "update_panel_meta", email)
    return {"email": email, **overrides}


@router.get("/gifts/{token}", response_model=AdminGiftResponse)
async def admin_get_gift(
    token: str,
    service_data: ServiceDataModel = Depends(get_service_data),
):
    """Admin: get gift link by token."""
    gift = await service_data.gifts.get_by(token=token)
    if not gift:
        raise HTTPException(status_code=404, detail="Gift not found")
    return {
        "token": gift.token,
        "sender_tg_id": gift.sender_tg_id,
        "tariff_id": gift.tariff_id,
        "created_at": gift.created_at.isoformat() if gift.created_at else None,
        # `used_at` в модели — это момент активации подарка (аналог redeemed_at);
        # отдаём оба имени для совместимости с клиентом, который ожидает redeemed_at.
        "redeemed_at": gift.used_at.isoformat() if gift.used_at else None,
        "used_at": gift.used_at.isoformat() if gift.used_at else None,
        "recipient_tg_id": gift.recipient_tg_id,
        # В БД колонка `email` хранит email получателя (аналог recipient_email).
        "recipient_email": gift.email,
    }


@router.get("/gifts", response_model=AdminGiftsListResponse)
async def admin_list_gifts(
    sender_tg_id: int = Query(None, description="Filter by sender Telegram ID"),
    service_data: ServiceDataModel = Depends(get_service_data),
):
    """Admin: list gift links, optionally filtered by sender_tg_id."""
    gifts = await service_data.gifts.get_all()
    if sender_tg_id is not None:
        gifts = [g for g in gifts if g.sender_tg_id == sender_tg_id]
    return {
        "gifts": [
            {
                "token": g.token,
                "sender_tg_id": g.sender_tg_id,
                "tariff_id": g.tariff_id,
                "created_at": g.created_at.isoformat() if g.created_at else None,
                # См. замечание про `used_at` ↔ `redeemed_at` в admin_get_gift.
                "redeemed_at": g.used_at.isoformat() if g.used_at else None,
                "used_at": g.used_at.isoformat() if g.used_at else None,
                "recipient_tg_id": g.recipient_tg_id,
                "recipient_email": g.email,
            }
            for g in gifts
        ]
    }


@router.get("/tariffs/{tariff_id}", response_model=AdminTariffDetailResponse)
async def admin_get_tariff(
    tariff_id: int,
    pool=Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
):
    """Admin: get tariff by id."""
    tariff = await service_data.tariffs.get_data(tariff_id, conn=pool)
    if not tariff:
        raise HTTPException(status_code=404, detail="Tariff not found")
    return {
        "id": tariff.id,
        "name_tariff": tariff.name_tariff,
        "amount": tariff.amount,
        "period": tariff.period,
        "traffic_limit": tariff.traffic_limit,
    }


@router.get("/referrals/links/{tg_id}", response_model=AdminReferralLinkResponse)
async def admin_get_referral_link(
    tg_id: int,
    service_data: ServiceDataModel = Depends(get_service_data),
):
    """Get referral link for a user."""
    existing = await service_data.referral_links.get_by(referrer_tg_id=tg_id)
    if existing:
        return {"token": existing.token, "referrer_tg_id": existing.referrer_tg_id}
    return {"token": None, "referrer_tg_id": tg_id}


@router.post("/referrals/links", response_model=AdminReferralLinkResponse)
async def admin_create_referral_link(
    tg_id: int = Query(..., description="Referrer Telegram ID"),
    pool=Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
):
    """Get existing or create new referral link for a user."""
    import uuid
    existing = await service_data.referral_links.get_by(referrer_tg_id=tg_id)
    if existing:
        return {"token": existing.token, "referrer_tg_id": existing.referrer_tg_id}
    token = f"ref_{uuid.uuid4().hex[:12]}"
    from models.referrals.referral_link import ReferralLink
    link = ReferralLink(referrer_tg_id=tg_id, token=token)
    await service_data.referral_links.save_data(pool, link, token=link.token)
    return {"token": link.token, "referrer_tg_id": link.referrer_tg_id}


@router.get("/referrals/links/by-token/{token}", response_model=AdminReferralLinkDetailResponse)
async def admin_get_referral_link_by_token(
    token: str,
    service_data: ServiceDataModel = Depends(get_service_data),
):
    """Get referral link by token."""
    link = await service_data.referral_links.get_by(token=token)
    if not link:
        raise HTTPException(status_code=404, detail="Referral link not found")
    return {
        "token": link.token,
        "referrer_tg_id": link.referrer_tg_id,
        "created_at": link.created_at.isoformat() if link.created_at else None,
        "id": link.id,
    }


@router.get("/referrals/stats/{tg_id}", response_model=AdminReferralStatsResponse)
async def admin_get_referral_stats(
    tg_id: int,
    pool=Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
):
    """Get referral stats for a user."""
    link = await service_data.referral_links.get_by(referrer_tg_id=tg_id)
    link_id = link.id if link else None

    redemptions = await service_data.data_service.referral_redemptions.get_all(pool)
    referral_count = sum(1 for r in redemptions if r.referral_link_id == link_id) if redemptions else 0

    rewards = await service_data.data_service.referral_rewards.get_all(pool)
    user_rewards = [r for r in rewards if r.referrer_tg_id == tg_id] if rewards else []
    rewards_total = sum(float(r.reward_value) for r in user_rewards)

    user = await service_data.users.get_data(tg_id)
    balance = user.balance if user else 0.0

    return {
        "referral_count": referral_count,
        "rewards_count": len(user_rewards),
        "rewards_total": rewards_total,
        "balance": balance,
    }


@router.get("/tariffs", response_model=AdminTariffsListResponse)
async def admin_list_tariffs(
    pool=Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
):
    """Admin: list all tariffs."""
    tariffs = await service_data.tariffs.get_all(conn=pool)
    return {"tariffs": [AdminTariffItem.from_tariff(t) for t in tariffs]}


@destructive_router.post("/users/{tg_id}/delete", response_model=AdminDeleteUserResponse)
async def admin_delete_user(
    tg_id: int,
    principal: AdminPrincipal = Depends(verify_admin_actor),
    pool=Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
    cache: CacheService = Depends(get_cache),
):
    """Admin: delete a user and all their keys.

    User удаляется всегда. Per-key XUI delete: success → key row+cache удалены;
    fail → key row остаётся (orphan, sweep в фоновой задаче), фиксируется в
    keys_failed. Возвращает {deleted_user, keys_deleted, keys_failed}.
    """
    user = await service_data.users.get_data(tg_id)
    if not user:
        user = await service_data.data_service.users.get(pool, tg_id=tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    keys_result = await service_data.keys.get_by(tg_id=tg_id)
    if keys_result is None:
        keys = []
    elif isinstance(keys_result, list):
        keys = [k for k in keys_result if k is not None]
    else:
        keys = [keys_result]

    data_service = DataService()
    _, _, xui = build_key_services(pool, service_data, service_data.cache_service, data_service)

    keys_deleted = 0
    keys_failed: list[dict] = []
    for key in keys:
        try:
            ok = await xui.delete_client(key.email, key.inbound_id, key.client_id)
        except Exception as e:
            ok = False
            keys_failed.append({"email": key.email, "error": str(e)})
            continue
        if ok:
            await service_data.data_service.keys.delete(pool, email=key.email)
            await service_data.cache_service.keys.delete(CacheKeyManager.key(key.email))
            keys_deleted += 1
        else:
            keys_failed.append({"email": key.email, "error": "panel delete returned False"})

    # Аудит сразу после XUI-мутаций по ключам, до удаления user-строки —
    # если users.delete упадёт, деструктивные panel-операции уже в журнале.
    await AuditLogger(pool).record(principal.admin_tg_id, "delete_user", str(tg_id))
    await service_data.data_service.users.delete(pool, tg_id=tg_id)
    await service_data.cache_service.users.delete(CacheKeyManager.user(tg_id))
    if keys_failed:
        logger.warning(
            "delete_user: часть ключей не удалена из панели",
            extra={"tg_id": tg_id, "failed": keys_failed},
        )
    return {"deleted_user": True, "keys_deleted": keys_deleted, "keys_failed": keys_failed}


@router.get("/keys", response_model=AdminKeysListResponse)
async def admin_list_keys(
    response: Response,
    limit: Optional[int] = Query(None, ge=1, le=1000, description="Page size; omit for full list (legacy behaviour)"),
    offset: int = Query(0, ge=0),
    service_data: ServiceDataModel = Depends(get_service_data),
):
    """Admin: list all keys."""
    keys = await service_data.keys.get_all()
    response.headers["X-Total-Count"] = str(len(keys))
    if limit is not None:
        keys = keys[offset:offset + limit]
    return {"keys": [AdminKeyItem.from_key(k) for k in keys]}


@router.get("/payments", response_model=AdminPaymentsListResponse)
async def admin_list_payments(
    response: Response,
    limit: Optional[int] = Query(None, ge=1, le=1000, description="Page size; omit for full list (legacy behaviour)"),
    offset: int = Query(0, ge=0),
    service_data: ServiceDataModel = Depends(get_service_data),
):
    """Admin: list all payments."""
    payments = await service_data.payments.get_all()
    response.headers["X-Total-Count"] = str(len(payments))
    if limit is not None:
        payments = payments[offset:offset + limit]
    return {"payments": [AdminPaymentItem.from_payment(p) for p in payments]}


def _get_sync_scheduler():
    """Достаёт живой SyncScheduler из app.state (см. app/main.py lifespan).

    lifespan сохраняет create_scheduler().sync_scheduler как app.state.sync_scheduler.
    В тестах туда можно подсунуть фейк.
    """
    from app.main import app
    return getattr(app.state, "sync_scheduler", None)


@destructive_router.post("/sync", response_model=AdminSyncStartedResponse, status_code=202)
async def admin_sync(
    principal: AdminPrincipal = Depends(verify_admin_actor),
    pool: asyncpg.Pool = Depends(get_pool),
):
    """Запуск фоновой синхронизации. 202 + job_id; 409 если уже идёт.

    Раньше был синхронным (блокировал request на время sync_cache). Теперь
    запускает asyncio.Task через SyncScheduler.start_job и сразу отдаёт job_id.
    Статус — через GET /admin/sync/{job_id}.
    """
    scheduler = _get_sync_scheduler()
    if scheduler is None:
        raise HTTPException(status_code=503, detail="Scheduler unavailable")

    job_id, existing = await scheduler.start_job()
    if job_id is None:
        # Уже идёт: отдаём существующий job_id для статуса.
        raise HTTPException(
            status_code=409,
            detail={"detail": "sync already running", "job_id": existing},
        )

    await AuditLogger(pool).record(principal.admin_tg_id, "sync", job_id)
    return Response(
        status_code=202,
        content=f'{{"job_id":"{job_id}","status":"running"}}',
        media_type="application/json",
    )


@destructive_router.get("/sync/{job_id}", response_model=AdminSyncStatusResponse)
async def admin_sync_status(
    job_id: str,
    principal: AdminPrincipal = Depends(verify_admin_actor),
):
    """Статус фоновой синхронизации по job_id."""
    scheduler = _get_sync_scheduler()
    if scheduler is None:
        raise HTTPException(status_code=503, detail="Scheduler unavailable")

    js = await scheduler.get_job(job_id)
    if js is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"status": js.status, "result": js.result, "error": js.error}


# --- Управление API-ключами внешних клиентов (Этап 4) -------------------

@destructive_router.post("/api-clients", response_model=ApiClientCreatedResponse)
async def create_api_client(
    body: ApiClientCreateRequest,
    pool: asyncpg.Pool = Depends(get_pool),
    principal: AdminPrincipal = Depends(verify_admin_actor),
):
    """Создаёт нового внешнего API-клиента. Возвращает сырой ключ
    (`api_key`) один раз — дальше он нигде не восстанавливается."""
    client, raw_key = await ApiClientService(pool).create(body.name, body.scopes)
    await AuditLogger(pool).record(principal.admin_tg_id, "create_api_client", body.name)
    return ApiClientCreatedResponse(**ApiClientResponse.from_client(client).model_dump(), api_key=raw_key)


@destructive_router.get("/api-clients", response_model=ApiClientsListResponse)
async def list_api_clients(
    pool: asyncpg.Pool = Depends(get_pool),
    principal: AdminPrincipal = Depends(verify_admin_actor),
):
    clients = await ApiClientService(pool).list_all()
    return {"clients": [ApiClientResponse.from_client(c) for c in clients]}


@destructive_router.post("/api-clients/{client_id}/revoke", response_model=ApiClientResponse)
async def revoke_api_client(
    client_id: int,
    pool: asyncpg.Pool = Depends(get_pool),
    principal: AdminPrincipal = Depends(verify_admin_actor),
):
    client = await ApiClientService(pool).revoke(client_id)
    if not client:
        raise HTTPException(status_code=404, detail="API client not found")
    await AuditLogger(pool).record(principal.admin_tg_id, "revoke_api_client", str(client_id))
    return ApiClientResponse.from_client(client)


@destructive_router.post("/api-clients/{client_id}/rotate", response_model=ApiClientCreatedResponse)
async def rotate_api_client(
    client_id: int,
    pool: asyncpg.Pool = Depends(get_pool),
    principal: AdminPrincipal = Depends(verify_admin_actor),
):
    """Выпускает новый ключ для существующего клиента; старый ключ
    перестаёт работать немедленно (и реактивирует клиента, если был revoked)."""
    result = await ApiClientService(pool).rotate(client_id)
    if not result:
        raise HTTPException(status_code=404, detail="API client not found")
    client, raw_key = result
    await AuditLogger(pool).record(principal.admin_tg_id, "rotate_api_client", str(client_id))
    return ApiClientCreatedResponse(**ApiClientResponse.from_client(client).model_dump(), api_key=raw_key)

