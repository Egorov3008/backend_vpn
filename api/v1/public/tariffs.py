"""Публичные (per-client API-ключ) эндпоинты для тарифов.

Переиспользует те же handler-функции, что и внутренний api/v1/tariffs.py —
меняется только auth-зависимость (verify_api_client вместо verify_bot_secret),
поэтому бизнес-логика не дублируется.
"""
from typing import List

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from api.v1.tariffs import get_tariff, list_tariffs
from app.auth import verify_api_client
from app.dependencies import get_pool, get_service_data
from app.rate_limit import rate_limit
from app.schemas.tariffs import TariffResponse
from config import AVAILABLE_RATES_LIST
from services.core.data.service import ServiceDataModel

router = APIRouter(prefix="/tariffs")

_deps = [
    Depends(verify_api_client(required_scopes=["tariffs:read"])),
    Depends(rate_limit("public-tariffs", times=60, seconds=60)),
]


def _is_public(tariff_id: int) -> bool:
    # Тот же список, что и в боте (AVAILABLE_RATES из .env)
    return tariff_id in AVAILABLE_RATES_LIST


async def public_list_tariffs(
    service_data: ServiceDataModel = Depends(get_service_data),
    pool: asyncpg.Pool = Depends(get_pool),
) -> List[TariffResponse]:
    tariffs = await list_tariffs(service_data=service_data, pool=pool)
    return [t for t in tariffs if _is_public(t.id)]


async def public_get_tariff(
    tariff_id: int,
    service_data: ServiceDataModel = Depends(get_service_data),
    pool: asyncpg.Pool = Depends(get_pool),
) -> TariffResponse:
    # Тариф вне AVAILABLE_RATES для публичного API неотличим от несуществующего
    if not _is_public(tariff_id):
        raise HTTPException(status_code=404, detail="Tariff not found")
    return await get_tariff(tariff_id=tariff_id, service_data=service_data, pool=pool)


# Путь без завершающего "/" — исторический пилотный контракт
# (GET /api/v1/public/tariffs), сохраняем как есть.
router.add_api_route(
    "",
    public_list_tariffs,
    methods=["GET"],
    response_model=List[TariffResponse],
    dependencies=_deps,
)
router.add_api_route(
    "/{tariff_id}",
    public_get_tariff,
    methods=["GET"],
    response_model=TariffResponse,
    dependencies=_deps,
)
