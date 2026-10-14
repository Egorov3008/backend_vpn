"""Публичные (per-client API-ключ) эндпоинты для платежей.

Переиспользует те же handler-функции, что и внутренний api/v1/payments.py
(YooKassa-интеграция, скидки, кеш) — меняется только auth-зависимость.
Webhook (`/payments/webhook`) сюда намеренно не выносится: он вызывается
только YooKassa (IP allowlist), а не внешними клиентами.
"""
from typing import List

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from api.v1.payments import (
    calculate_payment,
    create_payment,
    get_payment_history,
    get_payment_status,
)
from app.auth import verify_api_client
from app.dependencies import get_cache, get_pool, get_service_data
from app.rate_limit import rate_limit
from app.schemas.payments import (
    PaymentCalculateRequest,
    PaymentCalculateResponse,
    PaymentCreateRequest,
    PaymentCreateResponse,
    PaymentHistoryItem,
    PaymentStatusResponse,
)
from config import AVAILABLE_RATES_LIST
from services.cache.service import CacheService
from services.core.data.service import ServiceDataModel

router = APIRouter(prefix="/payments")


def _require_public_tariff(tariff_id: int) -> None:
    # Тот же список, что у GET /public/tariffs: скрытый тариф для внешнего клиента — 404,
    # иначе его можно было бы посчитать и купить, минуя публичный список.
    if tariff_id not in AVAILABLE_RATES_LIST:
        raise HTTPException(status_code=404, detail="Tariff not found")


async def public_calculate_payment(
    body: PaymentCalculateRequest,
    pool: asyncpg.Pool = Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
):
    _require_public_tariff(body.tariff_id)
    return await calculate_payment(body=body, pool=pool, service_data=service_data)


async def public_create_payment(
    body: PaymentCreateRequest,
    pool: asyncpg.Pool = Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
    cache: CacheService = Depends(get_cache),
):
    _require_public_tariff(body.tariff_id)
    tariff = await service_data.tariffs.get_data(body.tariff_id, conn=pool)
    if tariff and (not tariff.amount or tariff.amount <= 0):
        # Бесплатные тарифы не оплачиваются; как и в мини-приложении.
        raise HTTPException(status_code=400, detail="Tariff is not purchasable")
    # amount и referral_discount от внешнего клиента не принимаем: иначе можно задать себе
    # скидку любого размера. Сумму считает только бэкенд, как в мини-приложении.
    safe_body = PaymentCreateRequest(
        tg_id=body.tg_id,
        tariff_id=body.tariff_id,
        number_of_months=body.number_of_months,
        operation=body.operation,
        email=body.email,
        customer_email=body.customer_email,
    )
    return await create_payment(body=safe_body, pool=pool, service_data=service_data, cache=cache)

_read_deps = [
    Depends(verify_api_client(required_scopes=["payments:read"])),
    Depends(rate_limit("public-payments-read", times=60, seconds=60)),
]
_write_deps = [
    Depends(verify_api_client(required_scopes=["payments:write"])),
    Depends(rate_limit("public-payments-write", times=20, seconds=60)),
]

router.add_api_route(
    "/quotes",
    public_calculate_payment,
    methods=["POST"],
    response_model=PaymentCalculateResponse,
    dependencies=_read_deps,
)
router.add_api_route(
    "/create", public_create_payment, methods=["POST"], response_model=PaymentCreateResponse, dependencies=_write_deps
)
router.add_api_route(
    "/", get_payment_history, methods=["GET"], response_model=List[PaymentHistoryItem], dependencies=_read_deps
)
router.add_api_route(
    "/{payment_id}/status",
    get_payment_status,
    methods=["GET"],
    response_model=PaymentStatusResponse,
    dependencies=_read_deps,
)
