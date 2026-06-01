from typing import List

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from app.auth import verify_bot_secret
from app.dependencies import get_pool, get_service_data
from app.schemas.tariffs import TariffResponse
from services.core.data.service import ServiceDataModel

router = APIRouter(
    prefix="/tariffs",
    tags=["tariffs"],
    dependencies=[Depends(verify_bot_secret)],
)


@router.get("/", response_model=List[TariffResponse])
async def list_tariffs(
    service_data: ServiceDataModel = Depends(get_service_data),
    pool: asyncpg.Pool = Depends(get_pool),
) -> List[TariffResponse]:
    tariffs = await service_data.tariffs.get_all(conn=pool)
    return [TariffResponse.from_tariff(t) for t in tariffs]


@router.get("/{tariff_id}", response_model=TariffResponse)
async def get_tariff(
    tariff_id: int,
    service_data: ServiceDataModel = Depends(get_service_data),
    pool: asyncpg.Pool = Depends(get_pool),
) -> TariffResponse:
    tariff = await service_data.tariffs.get_data(tariff_id, conn=pool)
    if not tariff:
        raise HTTPException(status_code=404, detail="Tariff not found")
    return TariffResponse.from_tariff(tariff)
