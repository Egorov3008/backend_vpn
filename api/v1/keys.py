from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import verify_bot_secret
from app.dependencies import get_service_data
from app.schemas.keys import KeyResponse, KeyDetailResponse
from services.core.data.service import ServiceDataModel
from services.core.keys.service import KeyService

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
