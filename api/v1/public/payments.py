"""Публичные (per-client API-ключ) эндпоинты для платежей.

Переиспользует те же handler-функции, что и внутренний api/v1/payments.py
(YooKassa-интеграция, скидки, кеш) — меняется только auth-зависимость.
Webhook (`/payments/webhook`) сюда намеренно не выносится: он вызывается
только YooKassa (IP allowlist), а не внешними клиентами.
"""
from typing import List

from fastapi import APIRouter, Depends

from api.v1.payments import (
    calculate_payment,
    create_payment,
    get_payment_history,
    get_payment_status,
)
from app.auth import verify_api_client
from app.rate_limit import rate_limit
from app.schemas.payments import (
    PaymentCalculateResponse,
    PaymentCreateResponse,
    PaymentHistoryItem,
    PaymentStatusResponse,
)

router = APIRouter(prefix="/payments")

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
    calculate_payment,
    methods=["POST"],
    response_model=PaymentCalculateResponse,
    dependencies=_read_deps,
)
router.add_api_route(
    "/create", create_payment, methods=["POST"], response_model=PaymentCreateResponse, dependencies=_write_deps
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
