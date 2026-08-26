"""Пилотный публичный read-only роутер (Этап 4).

Первый эндпоинт под новую auth-схему для внешних клиентов (см.
app/auth.py::verify_api_client) — демонстрирует паттерн end-to-end:
API-ключ с scope, а не bot/web/mobile-mvp shared-секрет. Остальные
внутренние роутеры (keys/payments/users/...) им пока не защищаются —
им это не нужно, эндпоинт существует специально для внешних потребителей.
"""
from typing import List

import asyncpg
from fastapi import APIRouter, Depends

from app.auth import verify_api_client
from app.dependencies import get_pool, get_service_data
from app.rate_limit import rate_limit
from app.schemas.tariffs import TariffResponse
from services.core.data.service import ServiceDataModel

router = APIRouter(prefix="/public", tags=["public-api"])


@router.get(
    "/tariffs",
    response_model=List[TariffResponse],
    dependencies=[
        Depends(verify_api_client(required_scopes=["tariffs:read"])),
        Depends(rate_limit("public-tariffs", times=60, seconds=60)),
    ],
)
async def public_list_tariffs(
    service_data: ServiceDataModel = Depends(get_service_data),
    pool: asyncpg.Pool = Depends(get_pool),
) -> List[TariffResponse]:
    tariffs = await service_data.tariffs.get_all(conn=pool)
    return [TariffResponse.from_tariff(t) for t in tariffs]
