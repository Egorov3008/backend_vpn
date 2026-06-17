from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
import asyncpg

from app.auth import verify_bot_secret
from app.dependencies import get_service_data, get_pool, get_cache
from app.factories import build_key_services
from app.schemas.keys import KeyResponse, KeyDetailResponse, KeyCreateRequest, KeyRenewRequest
from config import DEFAULT_PRICING_PLAN, settings
from services.cache.key_manager import CacheKeyManager
from services.core.data.service import ServiceDataModel
from services.core.keys.service import KeyService
from services.core.user.utils.trial import TrialService
from services.core.gift import GiftLinkProvider
from database.service import DataService
from services.cache.service import CacheService

router = APIRouter(
    prefix="/keys",
    tags=["keys"],
    dependencies=[Depends(verify_bot_secret)],
)


def _normalize_get_by(result) -> list:
    """BaseData.get_by() возвращает list | объект | None — нормализуем к list."""
    if result is None:
        return []
    if isinstance(result, list):
        return result
    return [result]


@router.get("/", response_model=List[KeyResponse])
async def list_keys(
    tg_id: int = Query(..., description="Telegram user ID"),
    service_data: ServiceDataModel = Depends(get_service_data),
    pool: asyncpg.Pool = Depends(get_pool),
) -> List[KeyResponse]:
    # Always read from DB — cache can be stale when key was created outside the
    # backend request cycle (e.g., via the bot's own YooKassa webhook handler).
    keys = await service_data.data_service.keys.filter(pool, tg_id=tg_id)
    for k in keys:
        # Populate tariff name from tariffs cache
        if k.tariff_id:
            tariff = await service_data.tariffs.get_data(k.tariff_id, conn=pool)
            if tariff:
                k.name_tariff = tariff.name_tariff
        await service_data.cache_service.keys.set(CacheKeyManager.key(k.email), k)
    return [KeyResponse.from_key(k) for k in keys]


@router.get("/{email:path}", response_model=KeyDetailResponse)
async def get_key(
    email: str,
    service_data: ServiceDataModel = Depends(get_service_data),
    pool: asyncpg.Pool = Depends(get_pool),
) -> KeyDetailResponse:
    key = await service_data.keys.get_data(email)
    if not key:
        key = await service_data.data_service.keys.get(pool, email=email)
        if key:
            await service_data.cache_service.keys.set(CacheKeyManager.key(email), key)
    if not key:
        raise HTTPException(status_code=404, detail="Key not found")

    # Populate tariff name from tariffs cache
    if key.tariff_id:
        tariff = await service_data.tariffs.get_data(key.tariff_id, conn=pool)
        if tariff:
            key.name_tariff = tariff.name_tariff

    key_svc = KeyService(modul_data=service_data)
    detail = await key_svc.getter_key_data(email)
    if detail.get("error"):
        raise HTTPException(status_code=404, detail=detail.get("error_message", "Key not found"))

    return KeyDetailResponse(
        email=key.email,
        tg_id=key.tg_id,
        expiry_time=key.expiry_time,
        key=key.key,
        tariff_id=key.tariff_id,
        name_tariff=key.name_tariff,
        used_traffic=key.used_traffic,
        inbound_id=key.inbound_id,
        client_id=key.client_id,
        status_text=detail["status_text"],
        days_left=detail["days_left"],
        hours_left=detail["hours_left"],
        is_active=detail["is_active"],
        is_trial=detail["is_trial"],
        expiry_date=detail["expiry_date"],
    )


@router.post("/create", response_model=KeyResponse)
async def create_key(
    body: KeyCreateRequest,
    pool: asyncpg.Pool = Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
    cache: CacheService = Depends(get_cache),
) -> KeyResponse:
    """Create a free VPN key for a user"""
    tariff = await service_data.tariffs.get_data(body.tariff_id, conn=pool)
    if not tariff:
        raise HTTPException(status_code=404, detail="Tariff not found")
    if tariff.amount != 0:
        raise HTTPException(status_code=402, detail="Only free tariffs are allowed")

    user = await service_data.users.get_data(body.tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    data_service = DataService()
    create_key_svc, _, _ = build_key_services(pool, service_data, cache, data_service)

    result = await create_key_svc.proces(
        tg_id=body.tg_id,
        tariff=tariff,
        server_id=settings.xui_server_id,
        conn=pool,
        number_of_months=1,
    )

    if not result:
        raise HTTPException(status_code=500, detail="Failed to create key")

    key = await service_data.keys.get_data(result["email"])
    if not key:
        raise HTTPException(status_code=500, detail="Created key not found in database")

    return KeyResponse.from_key(key)


@router.post("/trial", response_model=KeyResponse)
async def create_trial_key(
    tg_id: int = Query(..., description="Telegram user ID"),
    gift_token: Optional[str] = Query(None, description="Optional gift token"),
    pool: asyncpg.Pool = Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
    cache: CacheService = Depends(get_cache),
) -> KeyResponse:
    """Create a free trial VPN key (sets user.trial = 1). If gift_token is provided, applies the gift."""
    user = await service_data.users.get_data(tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.trial != 0:
        raise HTTPException(status_code=403, detail="Trial already used")

    tariff = await service_data.tariffs.get_data(int(DEFAULT_PRICING_PLAN), conn=pool)
    if not tariff:
        raise HTTPException(status_code=404, detail="Trial tariff not found")

    data_service = DataService()
    create_key_svc, _, _ = build_key_services(pool, service_data, cache, data_service)

    result = await create_key_svc.proces(
        tg_id=tg_id,
        tariff=tariff,
        server_id=settings.xui_server_id,
        conn=pool,
        number_of_months=1,
    )

    if not result:
        raise HTTPException(status_code=500, detail="Failed to create trial key")

    key = await service_data.keys.get_data(result["email"])
    if not key:
        raise HTTPException(status_code=500, detail="Created key not found in database")

    await TrialService(service_data).installation_trial(tg_id, pool, trial=1)

    if gift_token:
        gift = await service_data.gifts.get_by(token=gift_token)
        if not gift:
            raise HTTPException(status_code=400, detail="Gift not found")
        if not gift.is_redeemable():
            raise HTTPException(status_code=400, detail="Gift already used or expired")
        gift_provider = GiftLinkProvider(
            gen_token=service_data.gift_token_gen,
            model_data=service_data,
        )
        await gift_provider.application(pool, gift, tg_id, key.email)

    return KeyResponse.from_key(key)


@router.delete("/{email}", status_code=204)
async def delete_key(
    email: str,
    tg_id: int = Query(..., description="Telegram user ID"),
    pool: asyncpg.Pool = Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
    cache: CacheService = Depends(get_cache),
):
    """Delete a VPN key"""
    key = await service_data.keys.get_data(email)
    if not key:
        key = await service_data.data_service.keys.get(pool, email=email)
        if key:
            await service_data.cache_service.keys.set(CacheKeyManager.key(email), key)
    # Fallback: treat the path param as client_id if not found by email
    if not key:
        key = await service_data.data_service.keys.get(pool, client_id=email)
        if key:
            email = key.email
            await service_data.cache_service.keys.set(CacheKeyManager.key(email), key)
    if not key:
        raise HTTPException(status_code=404, detail="Key not found")
    if key.tg_id != tg_id:
        raise HTTPException(status_code=403, detail="Key does not belong to this user")

    data_service = DataService()
    _, _, xui = build_key_services(pool, service_data, cache, data_service)

    deleted = await xui.delete_client(email, key.inbound_id, key.client_id)
    if not deleted:
        raise HTTPException(status_code=500, detail="Failed to delete key from server")

    await service_data.data_service.keys.delete(pool, email=email)
    await service_data.cache_service.keys.delete(CacheKeyManager.key(email))

    return None


@router.post("/{email}/renew", response_model=KeyResponse)
async def renew_key(
    email: str,
    body: KeyRenewRequest,
    pool: asyncpg.Pool = Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
    cache: CacheService = Depends(get_cache),
) -> KeyResponse:
    """Renew a VPN key"""
    key = await service_data.keys.get_data(email, pool)
    if not key:
        raise HTTPException(status_code=404, detail="Key not found")
    if key.tg_id != body.tg_id:
        raise HTTPException(status_code=403, detail="Key does not belong to this user")

    tariff = await service_data.tariffs.get_data(body.tariff_id, pool)
    if not tariff:
        raise HTTPException(status_code=404, detail="Tariff not found")
    if tariff.amount != 0:
        raise HTTPException(status_code=402, detail="Only free tariffs are allowed")

    user = await service_data.users.get_data(body.tg_id, pool)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    server = await service_data.servers.get_data(user.server_id, pool)
    if not server:
        from models.servers.server import get_env_server
        server = get_env_server()
    if not server:
        raise HTTPException(status_code=500, detail="Server not found")

    data_service = DataService()
    _, key_renewal_svc, _ = build_key_services(pool, service_data, cache, data_service)

    await key_renewal_svc.extension_key(
        key=key,
        conn=pool,
        server=server,
        tariff=tariff,
        number_of_months=body.number_of_months,
    )

    renewed_key = await service_data.keys.get_data(email, pool)
    if not renewed_key:
        raise HTTPException(status_code=500, detail="Renewed key not found in database")

    return KeyResponse.from_key(renewed_key)
