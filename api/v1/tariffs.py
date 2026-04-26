from typing import List

from fastapi import APIRouter, Depends, HTTPException

from app.auth import verify_bot_secret
from app.dependencies import get_service_data
from app.schemas.tariffs import TariffResponse
from services.core.data.service import ServiceDataModel

router = APIRouter(prefix="/tariffs", tags=["tariffs"])


@router.get("/", response_model=List[TariffResponse])
async def list_tariffs(
    _: None = Depends(verify_bot_secret),
    service_data: ServiceDataModel = Depends(get_service_data),
) -> List[TariffResponse]:
    tariffs = await service_data.tariffs.get_all()
    return [TariffResponse.from_tariff(t) for t in tariffs]


@router.get("/{tariff_id}", response_model=TariffResponse)
async def get_tariff(
    tariff_id: int,
    _: None = Depends(verify_bot_secret),
    service_data: ServiceDataModel = Depends(get_service_data),
) -> TariffResponse:
    tariff = await service_data.tariffs.get_data(tariff_id)
    if not tariff:
        raise HTTPException(status_code=404, detail="Tariff not found")
    return TariffResponse.from_tariff(tariff)
