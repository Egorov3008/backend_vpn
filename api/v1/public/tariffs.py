"""Публичные (per-client API-ключ) эндпоинты для тарифов.

Переиспользует те же handler-функции, что и внутренний api/v1/tariffs.py —
меняется только auth-зависимость (verify_api_client вместо verify_bot_secret),
поэтому бизнес-логика не дублируется.
"""
from typing import List

from fastapi import APIRouter, Depends

from api.v1.tariffs import get_tariff, list_tariffs
from app.auth import verify_api_client
from app.rate_limit import rate_limit
from app.schemas.tariffs import TariffResponse

router = APIRouter(prefix="/tariffs")

_deps = [
    Depends(verify_api_client(required_scopes=["tariffs:read"])),
    Depends(rate_limit("public-tariffs", times=60, seconds=60)),
]

# Путь без завершающего "/" — исторический пилотный контракт
# (GET /api/v1/public/tariffs), сохраняем как есть.
router.add_api_route(
    "",
    list_tariffs,
    methods=["GET"],
    response_model=List[TariffResponse],
    dependencies=_deps,
)
router.add_api_route(
    "/{tariff_id}",
    get_tariff,
    methods=["GET"],
    response_model=TariffResponse,
    dependencies=_deps,
)
