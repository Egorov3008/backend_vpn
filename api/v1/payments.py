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

router = APIRouter(
    prefix="/payments",
    tags=["payments"],
)


def _normalize_get_by(result) -> list:
    """BaseData.get_by() returns list | object | None — normalize to list."""
    if result is None:
        return []
    if isinstance(result, list):
        return result
    return [result]


def _extract_client_ip(request: Request) -> str | None:
    """Извлекает реальный IP клиента с учётом прокси-заголовков."""
    # Сначала проверяем X-Forwarded-For (может содержать цепочку: client, proxy1, proxy2)
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        # Берём первый IP — это реальный клиент
        client_ip_str = forwarded_for.split(",")[0].strip()
        if client_ip_str:
            return client_ip_str

    # Затем X-Real-IP
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip

    # Fallback на прямой client.host
    return request.client.host if request.client else None


def _check_webhook_ip(request: Request) -> bool:
    """Проверяет, что webhook приходит с одного из разрешённых IP YooKassa."""
    if settings.disable_webhook_ip_check:
        logger.info("IP проверка для webhook отключена (DEV MODE)")
        return True

    # Актуальные IP-диапазоны YooKassa (по состоянию на 2024–2025)
    # https://yookassa.ru/developers/using-api/webhooks
    allowed_networks = [
        ip_network("185.71.76.0/27"),
        ip_network("185.71.77.0/27"),
        ip_network("77.75.153.0/25"),
        ip_network("77.75.154.128/25"),
        ip_network("77.75.156.11/32"),
        ip_network("77.75.156.35/32"),
        ip_network("2a02:5180::/32"),
    ]

    client_ip_str = _extract_client_ip(request)
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
        logger.error("Ошибка при парсинге IP", extra={"client_ip": client_ip_str, "error": str(e)})
        return False


@router.post("/webhook")
async def payment_webhook(
    request: Request,
    body: PaymentWebhookBody,
    pool=Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
    cache: CacheService = Depends(get_cache),
):
    client_ip = request.client.host if request.client else "unknown"
    logger.debug("Webhook получен", extra={"event": body.event, "client_ip": client_ip, "type": body.type})

    if not _check_webhook_ip(request):
        logger.warning("Webhook IP не разрешен", extra={"client_ip": client_ip})
        raise HTTPException(status_code=403, detail="Webhook IP not allowed")

    if body.event != "payment.succeeded":
        logger.debug("Webhook игнорирован (не payment.succeeded)", extra={"event": body.event})
        return {"ok": True}

    payment_id = body.object.get("id")
    if not payment_id:
        logger.warning("Webhook без payment_id")
        raise HTTPException(status_code=400, detail="Missing payment id in webhook body")

    logger.debug("Начало обработки webhook платежа", extra={"payment_id": payment_id})

    data_service = DataService()
    payment_router = build_payment_router(pool, service_data, cache, data_service)
    await payment_router.route(payment_id)

    logger.info("Webhook платежа обработан", extra={"payment_id": payment_id})
    return {"ok": True}


@router.post("/create", response_model=PaymentCreateResponse)
async def create_payment(
    body: PaymentCreateRequest,
    pool=Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
    _=Depends(verify_bot_secret),
):
    logger.debug("Запрос create_payment", extra={"tg_id": body.tg_id, "tariff_id": body.tariff_id, "months": body.number_of_months, "operation": body.operation})

    tariff = await service_data.tariffs.get_data(body.tariff_id, conn=pool)
    if not tariff:
        logger.warning("Тариф не найден", extra={"tariff_id": body.tariff_id})
        raise HTTPException(status_code=404, detail="Tariff not found")

    logger.debug("Тариф загружен", extra={"tariff_id": body.tariff_id, "name": tariff.name_tariff, "amount": tariff.amount})

    if body.operation == "renew_key":
        if not body.email:
            logger.warning("Email не указан для renew_key операции")
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

    logger.debug("Сумма рассчитана", extra={"base": base, "discount_percent": discount_percent, "final_amount": amount})

    import yookassa
    yookassa.Configuration.account_id = settings.yookassa_shop_id
    yookassa.Configuration.secret_key = settings.yookassa_secret_key

    idempotency_key = str(uuid.uuid4())
    logger.debug("Отправка запроса в YooKassa", extra={"idempotency_key": idempotency_key, "amount": amount, "payment_type": payment_type})

    try:
        yk_payment = yookassa.Payment.create(
            {
                "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
                "confirmation": {
                    "type": "redirect",
                    "return_url": f"https://t.me/{settings.url_bot}",
                },
                "capture": True,
                "description": f"Помощь в ИТ {body.tg_id} {tariff.name_tariff} x{body.number_of_months}",
                "metadata": {
                    "tg_id": str(body.tg_id),
                    "payment_type": payment_type,
                },
            },
            idempotency_key,
        )
        logger.debug("YooKassa вернул платёж", extra={"payment_id": yk_payment.id, "status": yk_payment.status})
    except Exception as e:
        logger.error("YooKassa payment creation failed", extra={"error": str(e)})
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
    logger.debug("PaymentModel создан", extra={"payment_dict": payment.to_dict()})

    try:
        await service_data.payments.save_data(pool, payment, payment_id=payment_id)
        logger.debug("Платёж сохранён в БД и кеше", extra={"payment_id": payment_id})
    except Exception as e:
        logger.error("Ошибка при сохранении платежа", extra={"error": str(e), "payment_id": payment_id})
        raise HTTPException(status_code=500, detail="Failed to save payment")

    logger.info("Платёж успешно создан", extra={"payment_id": payment_id, "tg_id": body.tg_id, "amount": amount})

    return PaymentCreateResponse(
        payment_id=payment_id,
        confirmation_url=confirmation_url,
        amount=amount,
    )


@router.get("/", response_model=List[PaymentHistoryItem])
async def get_payment_history(
    tg_id: int = Query(..., description="Telegram user ID"),
    service_data: ServiceDataModel = Depends(get_service_data),
    _=Depends(verify_bot_secret),
) -> List[PaymentHistoryItem]:
    """Get payment history for a user"""
    logger.debug(f"Запрос истории платежей", extra={"tg_id": tg_id})
    result = await service_data.payments.get_by(tg_id=tg_id)
    payments = _normalize_get_by(result)
    logger.debug(f"Загружено платежей", extra={"tg_id": tg_id, "count": len(payments)})
    if payments:
        logger.debug(f"IDs платежей", extra={"payment_ids": [p.payment_id for p in payments]})
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
    service_data: ServiceDataModel = Depends(get_service_data),
    pool=Depends(get_pool),
    cache: CacheService = Depends(get_cache),
    _=Depends(verify_bot_secret),
) -> PaymentStatusResponse:
    """Get status of a specific payment"""
    logger.debug("Запрос статуса платежа", extra={"payment_id": payment_id, "tg_id": tg_id})
    payment = await service_data.payments.get_data(payment_id)
    if not payment:
        logger.warning("Платёж не найден", extra={"payment_id": payment_id})
        raise HTTPException(status_code=404, detail="Payment not found")

    logger.debug("Платёж загружен", extra={"payment_id": payment_id, "owner_tg_id": payment.tg_id, "status": payment.status})

    if payment.tg_id != tg_id:
        logger.warning("Платёж принадлежит другому пользователю", extra={"payment_id": payment_id, "requested_tg_id": tg_id, "actual_tg_id": payment.tg_id})
        raise HTTPException(status_code=403, detail="Payment does not belong to this user")

    if payment.status == "pending":
        try:
            import yookassa
            yookassa.Configuration.account_id = settings.yookassa_shop_id
            yookassa.Configuration.secret_key = settings.yookassa_secret_key

            logger.debug("Проверка статуса платежа в YooKassa", extra={"payment_id": payment_id})
            yk_payment = yookassa.Payment.find_one(payment_id)

            if yk_payment and hasattr(yk_payment, 'status'):
                yk_status = yk_payment.status
                logger.debug("Статус в YooKassa получен", extra={"payment_id": payment_id, "yk_status": yk_status})

                if yk_status == "succeeded":
                    logger.info("YooKassa подтвердил успех платежа, запускаем обработку", extra={"payment_id": payment_id})
                    try:
                        from database.service import DataService
                        data_service = DataService()
                        payment_router = build_payment_router(pool, service_data, cache, data_service)
                        await payment_router.route(payment_id)
                    except Exception as e:
                        logger.error("Ошибка при обработке платежа после YooKassa подтверждения", extra={"payment_id": payment_id, "error": str(e), "error_type": type(e).__name__}, exc_info=True)

                    # Перезагружаем платёж из БД (с DB-fallback) чтобы получить актуальный статус
                    payment = await service_data.payments.get_data(payment_id, conn=pool)
                    if not payment:
                        logger.warning("Платёж не найден после обработки", extra={"payment_id": payment_id})
                        raise HTTPException(status_code=404, detail="Payment not found after processing")

                elif yk_status == "canceled":
                    logger.info("YooKassa подтвердил отмену платежа, обновляем статус", extra={"payment_id": payment_id})
                    try:
                        canceled_payment = PaymentModel(
                            payment_id=payment.payment_id,
                            tg_id=payment.tg_id,
                            amount=payment.amount,
                            payment_type=payment.payment_type,
                            status="canceled",
                            number_of_months=payment.number_of_months,
                            discount_percent=payment.discount_percent,
                        )
                        await service_data.payments.update(pool, canceled_payment, search_data={"payment_id": payment_id})
                        payment = canceled_payment
                        logger.info("Статус платежа обновлен на canceled", extra={"payment_id": payment_id})
                    except Exception as e:
                        logger.warning("Ошибка при обновлении статуса на canceled", extra={"payment_id": payment_id, "error": str(e)})
        except Exception as e:
            logger.warning("Ошибка при проверке статуса в YooKassa", extra={"payment_id": payment_id, "error": str(e)})

    logger.info("Статус платежа возвращен", extra={"payment_id": payment_id, "status": payment.status})
    return PaymentStatusResponse(
        payment_id=payment.payment_id,
        status=payment.status,
        tg_id=payment.tg_id,
    )
