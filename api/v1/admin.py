from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
import asyncpg

from app.auth import verify_admin_or_bot
from app.dependencies import get_service_data, get_pool, get_cache
from app.factories import build_key_services
from app.schemas.users import UserResponse, UserUpdateRequest, UserRegisterRequest
from app.schemas.admin import (
    AdminGenerateKeyRequest,
    AdminMassRenewRequest,
    AdminChangeDateRequest,
    AdminChangeTariffRequest,
)
from models.stocks.stock import Stock
from database.service import DataService
from logger import logger
from models import User
from services.cache.key_manager import CacheKeyManager
from services.cache.service import CacheService
from services.core.data.service import ServiceDataModel
from services.core.keys.admin_report import KeyAdminReport
from services.core.keys.utils.reset import KeyResetter

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(verify_admin_or_bot)],
)


@router.get("/stats")
async def get_stats(
    service_data: ServiceDataModel = Depends(get_service_data),
):
    keys = await service_data.keys.get_all()
    users = await service_data.users.get_all()
    stats = await KeyAdminReport().get_summary_stats(keys)
    return {"total_users": len(users), **stats}


@router.get("/users", response_model=List[UserResponse])
async def list_users(
    service_data: ServiceDataModel = Depends(get_service_data),
) -> List[UserResponse]:
    users = await service_data.users.get_all()
    return [UserResponse.from_user(u) for u in users]


@router.get("/users/{tg_id}", response_model=UserResponse)
async def admin_get_user(
    tg_id: int,
    service_data: ServiceDataModel = Depends(get_service_data),
) -> UserResponse:
    user = await service_data.users.get_data(tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return UserResponse.from_user(user)


@router.get("/users/{tg_id}/stock")
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


@router.patch("/users/{tg_id}", response_model=UserResponse)
async def admin_update_user(
    tg_id: int,
    body: UserUpdateRequest,
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

    await service_data.users.update(pool, user, search_data={"tg_id": tg_id})
    return UserResponse.from_user(user)


@router.post("/keys/{email}/delete", status_code=204)
async def admin_delete_key(
    email: str,
    pool=Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
    cache: CacheService = Depends(get_cache),
):
    """Admin: delete any VPN key (no tg_id ownership check)."""
    key = await service_data.keys.get_data(email)
    if not key:
        key = await service_data.data_service.keys.get(pool, email=email)
        if key:
            await service_data.cache_service.keys.set(CacheKeyManager.key(email), key)
    if not key:
        raise HTTPException(status_code=404, detail="Key not found")

    data_service = DataService()
    _, _, xui = build_key_services(pool, service_data, cache, data_service)

    deleted = await xui.delete_client(email, key.inbound_id, key.client_id)
    if not deleted:
        raise HTTPException(status_code=500, detail="Failed to delete key from server")

    await service_data.data_service.keys.delete(pool, email=email)
    await service_data.cache_service.keys.delete(CacheKeyManager.key(email))
    return None


@router.get("/users/inactive")
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


@router.post("/users/inactive/delete")
async def delete_inactive_users(
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

    return {"deleted": deleted}


@router.post("/keys/generate")
async def admin_generate_key(
    body: AdminGenerateKeyRequest,
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

    server_id = body.server_id
    if body.inbound_id:
        inbound = await service_data.inbounds.get_data((server_id, body.inbound_id))
        if not inbound:
            inbound = await service_data.data_service.inbounds.get(pool, inbound_id=body.inbound_id)
        if inbound:
            server_id = inbound.server_id
            await cache.users.set(CacheKeyManager.temporary_inbound(body.tg_id), str(body.inbound_id))

    data_service = DataService()
    create_key, _, _ = build_key_services(pool, service_data, cache, data_service)

    result = await create_key.proces(
        tg_id=body.tg_id,
        tariff=tariff,
        server_id=server_id,
        conn=pool,
        number_of_months=body.number_of_months,
    )
    if not result:
        raise HTTPException(status_code=500, detail="Failed to create key")
    return result


@router.post("/keys/mass-renew")
async def admin_mass_renew(
    body: AdminMassRenewRequest,
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
            key.expiry_time = new_expiry

            await xui.extend_client_key(key)
            await service_data.keys.update(pool, key, {"email": key.email})
            await resetter.reset_key_after_renewal(pool, key)

            results.append({"email": email, "success": True, "new_expiry": new_expiry})
        except Exception as e:
            logger.error("Mass renew failed for key", email=email, error=str(e))
            results.append({"email": email, "success": False, "error": str(e)})

    success_count = sum(1 for r in results if r["success"])
    return {"total": len(body.emails), "success": success_count, "failed": len(body.emails) - success_count, "results": results}


@router.post("/keys/{email}/change-date")
async def admin_change_key_date(
    email: str,
    body: AdminChangeDateRequest,
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
    updated = await xui.extend_client_key(key)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update key in panel")
    await service_data.keys.update(pool, key, {"email": key.email})
    await resetter.reset_key_after_renewal(pool, key)
    return {"email": email, "expiry_time": body.expiry_time}


@router.post("/keys/{email}/change-tariff")
async def admin_change_key_tariff(
    email: str,
    body: AdminChangeTariffRequest,
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
    key.total_gb = tariff.traffic_limit
    key.limit_ip = tariff.limit_ip
    key.name_tariff = tariff.name_tariff

    updated = await xui.extend_client_key(key)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update key in panel")
    await service_data.keys.update(pool, key, {"email": key.email})
    await resetter.reset_key_after_renewal(pool, key)
    return {"email": email, "tariff_id": tariff.id}


@router.get("/gifts/{token}")
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
        "redeemed_at": gift.redeemed_at.isoformat() if gift.redeemed_at else None,
        "recipient_tg_id": gift.recipient_tg_id,
        "recipient_email": gift.recipient_email,
        "expiry_date": gift.expiry_date.isoformat() if gift.expiry_date else None,
    }


@router.get("/gifts")
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
                "redeemed_at": g.redeemed_at.isoformat() if g.redeemed_at else None,
                "recipient_tg_id": g.recipient_tg_id,
                "recipient_email": g.recipient_email,
                "expiry_date": g.expiry_date.isoformat() if g.expiry_date else None,
            }
            for g in gifts
        ]
    }


@router.get("/inbounds")
async def admin_list_inbounds(
    service_data: ServiceDataModel = Depends(get_service_data),
):
    """Admin: list all inbounds."""
    inbounds = await service_data.inbounds.get_all()
    return {"inbounds": inbounds}


@router.get("/tariffs/{tariff_id}")
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


@router.get("/referrals/links/{tg_id}")
async def admin_get_referral_link(
    tg_id: int,
    service_data: ServiceDataModel = Depends(get_service_data),
):
    """Get referral link for a user."""
    existing = await service_data.referral_links.get_by(referrer_tg_id=tg_id)
    if existing:
        return {"token": existing.token, "referrer_tg_id": existing.referrer_tg_id}
    return {"token": None, "referrer_tg_id": tg_id}


@router.post("/referrals/links")
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


@router.get("/referrals/links/by-token/{token}")
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


@router.get("/referrals/stats/{tg_id}")
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


@router.get("/tariffs")
async def admin_list_tariffs(
    pool=Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
):
    """Admin: list all tariffs."""
    tariffs = await service_data.tariffs.get_all(conn=pool)
    return {"tariffs": tariffs}


@router.post("/users/{tg_id}/delete", status_code=204)
async def admin_delete_user(
    tg_id: int,
    pool=Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
):
    """Admin: delete a user and all their keys."""
    user = await service_data.users.get_data(tg_id)
    if not user:
        user = await service_data.data_service.users.get(pool, tg_id=tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    keys_result = await service_data.keys.get_by(tg_id=tg_id)
    keys = []
    if keys_result is None:
        keys = []
    elif isinstance(keys_result, list):
        keys = [k for k in keys_result if k is not None]
    else:
        keys = [keys_result]

    data_service = DataService()
    _, _, xui = build_key_services(pool, service_data, service_data.cache_service, data_service)

    for key in keys:
        await xui.delete_client(key.email, key.inbound_id, key.client_id)
        await service_data.data_service.keys.delete(pool, email=key.email)
        await service_data.cache_service.keys.delete(CacheKeyManager.key(key.email))

    await service_data.data_service.users.delete(pool, tg_id=tg_id)
    await service_data.cache_service.users.delete(CacheKeyManager.user(tg_id))
    return None


@router.get("/keys")
async def admin_list_keys(
    service_data: ServiceDataModel = Depends(get_service_data),
):
    """Admin: list all keys."""
    keys = await service_data.keys.get_all()
    return {"keys": keys}


@router.get("/payments")
async def admin_list_payments(
    service_data: ServiceDataModel = Depends(get_service_data),
):
    """Admin: list all payments."""
    payments = await service_data.payments.get_all()
    return {"payments": payments}


@router.post("/sync")
async def admin_sync(
    pool=Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
):
    """Trigger manual cache and panel synchronization.

    Runs the same pipeline as the background scheduler (every 3h):
    1. Full cache reload from PostgreSQL
    2. Panel ↔ DB sync + traffic update
    """
    from background.scheduler import _sync_cache, _sync_panel

    cache_result = await _sync_cache(service_data, pool)
    panel_result = await _sync_panel(service_data, pool)

    has_error = (
        (cache_result or {}).get("status") == "error"
        or (panel_result or {}).get("status") == "error"
    )
    if has_error:
        raise HTTPException(
            status_code=500,
            detail={"cache": cache_result, "panel": panel_result},
        )

    return {
        "status": "success",
        "cache": cache_result,
        "panel": panel_result,
    }

