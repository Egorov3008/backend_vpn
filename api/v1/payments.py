import uuid
from typing import List
from ipaddress import ip_address, ip_network

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth import verify_bot_secret
from app.dependencies import get_service_data, get_pool, get_cache
from app.factories import build_payment_router
from app.schemas.payments import (
    PaymentWebhookBody,
    PaymentCreateRequest,
    PaymentCreateResponse,
    PaymentHistoryItem,
    PaymentStatusResponse,
)
from config import settings
from database.service import DataService
from models import PaymentModel
from services.cache.service import CacheService
from services.core.data.service import ServiceDataModel
from logger import logger

router = APIRouter(prefix="/payments", tags=["payments"])


def _normalize_get_by(result) -> list:
    """BaseData.get_by() returns list | object | None — normalize to list."""
    if result is None:
        return []
    if isinstance(result, list):
        return result
    return [result]


def _check_webhook_ip(request: Request) -> bool:
    """Проверяет, что webhook приходит с одного из разрешённых IP YooKassa."""
    if settings.disable_webhook_ip_check:
        logger.info("IP проверка для webhook отключена (DEV MODE)")
        return True

    # IP адреса YooKassa: 185.71.76.0/27 и 185.109.44.0/27
    allowed_networks = [
        ip_network("185.71.76.0/27"),
        ip_network("185.109.44.0/27"),
    ]

    client_ip_str = request.client.host if request.client else None
    if not client_ip_str:
        logger.warning("Webhook: не удалось определить IP клиента")
        return False

    try:
        client_ip = ip_address(client_ip_str)
        for network in allowed_networks:
            if client_ip in network:
                logger.info(f"Webhook IP валиден: {client_ip}")
                return True

        logger.warning(f"Webhook отклонён: IP {client_ip} не в списке разрешённых YooKassa")
        return False
    except ValueError as e:
        logger.error(f"Ошибка при парсинге IP: {client_ip_str}, error={str(e)}")
        return False


@router.post("/webhook")
async def payment_webhook(
    request: Request,
    body: PaymentWebhookBody,
    pool=Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
    cache: CacheService = Depends(get_cache),
):
    if not _check_webhook_ip(request):
        raise HTTPException(status_code=403, detail="Webhook IP not allowed")

    if body.event != "payment.succeeded":
        return {"ok": True}

    payment_id = body.object.get("id")
    if not payment_id:
        raise HTTPException(status_code=400, detail="Missing payment id in webhook body")

    data_service = DataService()
    payment_router = build_payment_router(pool, service_data, cache, data_service)
    await payment_router.route(payment_id)
    return {"ok": True}


@router.post("/create", response_model=PaymentCreateResponse)
async def create_payment(
    body: PaymentCreateRequest,
    _: None = Depends(verify_bot_secret),
    pool=Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
):
    tariff = await service_data.tariffs.get_data(body.tariff_id)
    if not tariff:
        raise HTTPException(status_code=404, detail="Tariff not found")

    if body.operation == "renew_key":
        if not body.email:
            raise HTTPException(status_code=422, detail="email required for renew_key operation")
        payment_type = f"renew_key|{body.email}"
    else:
        payment_type = f"create_key|{body.tariff_id}"

    base = tariff.amount * body.number_of_months
    discount_percent = 0
    if body.number_of_months >= 2 and settings.discounts > 0:
        discount_percent = settings.discounts
        amount = round(base * (1 - settings.discounts / 100), 2)
    else:
        amount = base

    import yookassa
    yookassa.Configuration.account_id = settings.yookassa_shop_id
    yookassa.Configuration.secret_key = settings.yookassa_secret_key

    idempotency_key = str(uuid.uuid4())
    try:
        yk_payment = yookassa.Payment.create(
            {
                "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
                "confirmation": {
                    "type": "redirect",
                    "return_url": f"https://t.me/{settings.url_bot}",
                },
                "capture": True,
                "description": f"VPN {tariff.name_tariff} x{body.number_of_months}",
                "metadata": {
                    "tg_id": str(body.tg_id),
                    "payment_type": payment_type,
                },
            },
            idempotency_key,
        )
    except Exception as e:
        logger.error("YooKassa payment creation failed", error=str(e))
        raise HTTPException(status_code=502, detail="Payment provider error")

    payment_id = yk_payment.id
    confirmation_url = yk_payment.confirmation.confirmation_url

    payment = PaymentModel(
        payment_id=payment_id,
        tg_id=body.tg_id,
        amount=amount,
        payment_type=payment_type,
        status="pending",
        number_of_months=body.number_of_months,
        discount_percent=discount_percent,
    )
    await service_data.payments.save_data(pool, payment, payment_id=payment_id)

    return PaymentCreateResponse(
        payment_id=payment_id,
        confirmation_url=confirmation_url,
        amount=amount,
    )


@router.get("/", response_model=List[PaymentHistoryItem])
async def get_payment_history(
    tg_id: int = Query(..., description="Telegram user ID"),
    _: None = Depends(verify_bot_secret),
    service_data: ServiceDataModel = Depends(get_service_data),
) -> List[PaymentHistoryItem]:
    """Get payment history for a user"""
    logger.info(f"Fetching payment history for tg_id={tg_id}")
    result = await service_data.payments.get_by(tg_id=tg_id)
    payments = _normalize_get_by(result)
    logger.info(f"Found {len(payments)} payments for tg_id={tg_id}")
    return [
        PaymentHistoryItem(
            payment_id=p.payment_id,
            tg_id=p.tg_id,
            amount=p.amount,
            status=p.status,
            payment_type=p.payment_type,
            created_at=p.created_at,
        )
        for p in payments
    ]


@router.get("/{payment_id}/status", response_model=PaymentStatusResponse)
async def get_payment_status(
    payment_id: str,
    tg_id: int = Query(..., description="Telegram user ID"),
    _: None = Depends(verify_bot_secret),
    service_data: ServiceDataModel = Depends(get_service_data),
) -> PaymentStatusResponse:
    """Get status of a specific payment"""
    logger.info(f"Fetching payment status for payment_id={payment_id}, tg_id={tg_id}")
    payment = await service_data.payments.get_data(payment_id)
    if not payment:
        logger.warning(f"Payment not found: payment_id={payment_id}")
        raise HTTPException(status_code=404, detail="Payment not found")

    if payment.tg_id != tg_id:
        logger.warning(f"Payment belongs to different user: payment_id={payment_id}, expected_tg_id={tg_id}, actual_tg_id={payment.tg_id}")
        raise HTTPException(status_code=403, detail="Payment does not belong to this user")

    logger.info(f"Payment status for payment_id={payment_id}: {payment.status}")
    return PaymentStatusResponse(
        payment_id=payment.payment_id,
        status=payment.status,
        tg_id=payment.tg_id,
    )
