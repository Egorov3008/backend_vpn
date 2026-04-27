from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
import asyncpg

from app.auth import verify_bot_secret
from app.dependencies import get_service_data, get_pool, get_cache
from app.factories import build_key_services
from app.schemas.keys import KeyResponse, KeyDetailResponse, KeyCreateRequest, KeyRenewRequest
from services.core.data.service import ServiceDataModel
from services.core.keys.service import KeyService
from database.service import DataService
from services.cache.service import CacheService

router = APIRouter(prefix="/keys", tags=["keys"])


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
    _: None = Depends(verify_bot_secret),
    service_data: ServiceDataModel = Depends(get_service_data),
) -> List[KeyResponse]:
    result = await service_data.keys.get_by(tg_id=tg_id)
    keys = _normalize_get_by(result)
    return [KeyResponse.from_key(k) for k in keys]


@router.get("/{email:path}", response_model=KeyDetailResponse)
async def get_key(
    email: str,
    _: None = Depends(verify_bot_secret),
    service_data: ServiceDataModel = Depends(get_service_data),
) -> KeyDetailResponse:
    key = await service_data.keys.get_data(email)
    if not key:
        raise HTTPException(status_code=404, detail="Key not found")

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
        total_gb=key.total_gb,
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
    _: None = Depends(verify_bot_secret),
    pool: asyncpg.Pool = Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
    cache: CacheService = Depends(get_cache),
) -> KeyResponse:
    """Create a free VPN key for a user"""
    # Check tariff exists and is free
    tariff = await service_data.tariffs.get_data(body.tariff_id)
    if not tariff:
        raise HTTPException(status_code=404, detail="Tariff not found")
    if tariff.amount != 0:
        raise HTTPException(status_code=402, detail="Only free tariffs are allowed")

    # Check user exists
    user = await service_data.users.get_data(body.tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Build key services
    data_service = DataService()
    create_key_svc, _, _ = build_key_services(pool, service_data, cache, data_service)

    # Create key (default server_id=2, number_of_months=1)
    result = await create_key_svc.proces(
        tg_id=body.tg_id,
        tariff=tariff,
        server_id=2,
        conn=pool,
        number_of_months=1,
    )

    if not result:
        raise HTTPException(status_code=500, detail="Failed to create key")

    # Fetch and return created key
    key = await service_data.keys.get_data(result["email"])
    if not key:
        raise HTTPException(status_code=500, detail="Created key not found in database")

    return KeyResponse.from_key(key)


@router.delete("/{email}", status_code=204)
async def delete_key(
    email: str,
    tg_id: int = Query(..., description="Telegram user ID"),
    _: None = Depends(verify_bot_secret),
    pool: asyncpg.Pool = Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
    cache: CacheService = Depends(get_cache),
):
    """Delete a VPN key"""
    # Check key exists and belongs to user
    key = await service_data.keys.get_data(email)
    if not key:
        raise HTTPException(status_code=404, detail="Key not found")
    if key.tg_id != tg_id:
        raise HTTPException(status_code=403, detail="Key does not belong to this user")

    # Build key services to get XUI session
    data_service = DataService()
    _, _, xui = build_key_services(pool, service_data, cache, data_service)

    # Delete from 3x-UI
    deleted = await xui.delete_client(email, key.inbound_id, key.client_id)
    if not deleted:
        raise HTTPException(status_code=500, detail="Failed to delete key from server")

    # Delete from database
    await service_data.keys.delete(pool, email=email)

    return None


@router.post("/{email}/renew", response_model=KeyResponse)
async def renew_key(
    email: str,
    body: KeyRenewRequest,
    _: None = Depends(verify_bot_secret),
    pool: asyncpg.Pool = Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
    cache: CacheService = Depends(get_cache),
) -> KeyResponse:
    """Renew a VPN key"""
    # Check key exists and belongs to user
    key = await service_data.keys.get_data(email)
    if not key:
        raise HTTPException(status_code=404, detail="Key not found")
    if key.tg_id != body.tg_id:
        raise HTTPException(status_code=403, detail="Key does not belong to this user")

    # Check tariff exists and is free
    tariff = await service_data.tariffs.get_data(body.tariff_id)
    if not tariff:
        raise HTTPException(status_code=404, detail="Tariff not found")
    if tariff.amount != 0:
        raise HTTPException(status_code=402, detail="Only free tariffs are allowed")

    # Get server
    server = await service_data.servers.get_data(key.inbound_id)
    if not server:
        raise HTTPException(status_code=500, detail="Server not found")

    # Build key services
    data_service = DataService()
    _, key_renewal_svc, _ = build_key_services(pool, service_data, cache, data_service)

    # Renew key
    await key_renewal_svc.extension_key(
        key=key,
        conn=pool,
        server=server,
        tariff=tariff,
        number_of_months=body.number_of_months,
    )

    # Fetch and return renewed key
    renewed_key = await service_data.keys.get_data(email)
    if not renewed_key:
        raise HTTPException(status_code=500, detail="Renewed key not found in database")

    return KeyResponse.from_key(renewed_key)
