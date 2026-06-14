import uuid
from typing import List, Optional
from ipaddress import ip_address, ip_network

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth import verify_bot_secret
from app.dependencies import get_service_data, get_pool, get_cache
from app.factories import build_payment_router
from app.schemas.payments import (
    PaymentWebhookBody,
    PaymentCreateRequest,
    PaymentCreateResponse,
    PaymentCalculateRequest,
    PaymentCalculateResponse,
    PaymentHistoryItem,
    PaymentStatusResponse,
)
from config import settings, REFERRAL_DISCOUNT_PERCENT, DISCOUNTS
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


def _calculate_payment_amount(
    tariff_amount: float,
    number_of_months: int,
    referral_discount_from_request: Optional[float],
    user_referral_id: Optional[int],
    user_check_referral: bool,
    user_tg_id: int,
    user_balance: float = 0.0,
) -> dict:
    """
    Рассчитывает сумму платежа со всеми скидками.

    Returns:
        dict с полями:
        - base_amount: базовая сумма
        - volume_discount_percent: % скидки за объём
        - volume_discount_amount: сумма скидки за объём
        - referral_discount_amount: сумма реферальной скидки (10% для приглашённых)
        - balance_discount_amount: сумма скидки за счёт баланса реферера
        - final_amount: итоговая сумма
        - has_volume_discount: bool
        - has_referral_discount: bool
        - has_balance_discount: bool
    """
    from config import MIN_PAYMENT_AMOUNT

    # 1. Базовая сумма
    base_amount = tariff_amount * number_of_months

    # 2. Скидка за объём (от 2 месяцев)
    volume_discount_percent = DISCOUNTS if number_of_months >= 2 and DISCOUNTS > 0 else 0
    amount_after_volume = round(base_amount * (1 - volume_discount_percent / 100), 2) if volume_discount_percent > 0 else base_amount
    volume_discount_amount = round(base_amount - amount_after_volume, 2)

    # 3. Реферальная скидка 10% (для приглашённых)
    referral_discount_amount = 0.0

    # Если передана скидка от бота — используем её (приоритет)
    if referral_discount_from_request is not None and referral_discount_from_request > 0:
        referral_discount_amount = referral_discount_from_request
    else:
        # Иначе рассчитываем по старой логике (из БД)
        if user_referral_id is not None and not user_check_referral and user_referral_id != user_tg_id:
            referral_discount_amount = round(amount_after_volume * REFERRAL_DISCOUNT_PERCENT, 2)

    # 4. Скидка за счёт баланса (для рефереров, у кого есть balance > 0)
    #    Нельзя списать весь баланс — минимальная сумма платежа 10₽
    balance_discount_amount = 0.0
    amount_after_referral = amount_after_volume - referral_discount_amount
    if user_balance > 0 and amount_after_referral > MIN_PAYMENT_AMOUNT:
        max_discount = round(amount_after_referral - MIN_PAYMENT_AMOUNT, 2)
        balance_discount_amount = round(min(user_balance, max_discount), 2) if max_discount > 0 else 0.0

    # 5. Итоговая сумма
    final_amount = round(amount_after_referral - balance_discount_amount, 2)

    return {
        "base_amount": base_amount,
        "volume_discount_percent": volume_discount_percent,
        "volume_discount_amount": volume_discount_amount,
        "referral_discount_amount": referral_discount_amount,
        "balance_discount_amount": balance_discount_amount,
        "final_amount": final_amount,
        "has_volume_discount": volume_discount_percent > 0,
        "has_referral_discount": referral_discount_amount > 0,
        "has_balance_discount": balance_discount_amount > 0,
    }


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


@router.post("/calculate", response_model=PaymentCalculateResponse)
async def calculate_payment(
    body: PaymentCalculateRequest,
    pool=Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
    _=Depends(verify_bot_secret),
):
    """Расчёт стоимости платежа без создания платежа.

    Бот вызывает этот endpoint перед отображением окна оплаты,
    чтобы получить актуальную сумму со всеми скидками.
    """
    logger.debug("Запрос calculate_payment", extra={
        "tg_id": body.tg_id,
        "tariff_id": body.tariff_id,
        "months": body.number_of_months,
        "operation": body.operation,
    })

    tariff = await service_data.tariffs.get_data(body.tariff_id, conn=pool)
    if not tariff:
        logger.warning("Тариф не найден", extra={"tariff_id": body.tariff_id})
        raise HTTPException(status_code=404, detail="Tariff not found")

    # Загружаем пользователя для расчёта скидок
    user = await service_data.users.get_data(body.tg_id, conn=pool)
    user_referral_id = getattr(user, "referral_id", None) if user else None
    user_check_referral = getattr(user, "check_referral", False) if user else False
    user_balance = getattr(user, "balance", 0.0) if user else 0.0

    result = _calculate_payment_amount(
        tariff_amount=tariff.amount,
        number_of_months=body.number_of_months,
        referral_discount_from_request=None,  # Бот не передаёт — рассчитываем на бэкенде
        user_referral_id=user_referral_id,
        user_check_referral=user_check_referral,
        user_tg_id=body.tg_id,
        user_balance=user_balance,
    )

    logger.info("Расчёт платежа завершён", extra={
        "tg_id": body.tg_id,
        "base_amount": result["base_amount"],
        "final_amount": result["final_amount"],
        "volume_discount": result["volume_discount_amount"],
        "referral_discount": result["referral_discount_amount"],
        "balance_discount": result["balance_discount_amount"],
    })

    return PaymentCalculateResponse(**result)


@router.post("/create", response_model=PaymentCreateResponse)
async def create_payment(
    body: PaymentCreateRequest,
    pool=Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
    cache: CacheService = Depends(get_cache),
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

        # КРИТИЧНО: сохраняем выбранный тариф в кеш для продления триального ключа
        # Это нужно, чтобы KeyRenewalService использовал правильный тариф, а не fallback на key.tariff_id
        from datetime import timedelta
        renewal_cache_key = f"renewal_tariff_{body.email}"
        try:
            await cache.tariffs.temporary_set(
                renewal_cache_key,
                ttl=timedelta(minutes=15),
                tariff_id=body.tariff_id,
            )
            logger.info(
                "[Цена:CreatePayment] Выбранный тариф сохранён в backend кеш для продления",
                email=body.email,
                tariff_id=body.tariff_id,
                cache_key=renewal_cache_key,
            )
        except Exception as e:
            logger.warning(
                "[Цена:CreatePayment] Не удалось сохранить тариф в кеш",
                email=body.email,
                tariff_id=body.tariff_id,
                error=str(e),
            )
    else:
        payment_type = f"create_key|{body.tariff_id}"

    # Загружаем пользователя для расчёта скидок
    user = await service_data.users.get_data(body.tg_id, conn=pool)
    user_referral_id = getattr(user, "referral_id", None) if user else None
    user_check_referral = getattr(user, "check_referral", False) if user else False
    user_balance = getattr(user, "balance", 0.0) if user else 0.0

    # Рассчитываем сумму с использованием общей функции
    result = _calculate_payment_amount(
        tariff_amount=tariff.amount,
        number_of_months=body.number_of_months,
        referral_discount_from_request=body.referral_discount if body.referral_discount and body.referral_discount > 0 else None,
        user_referral_id=user_referral_id,
        user_check_referral=user_check_referral,
        user_tg_id=body.tg_id,
        user_balance=user_balance,
    )

    final_amount = result["final_amount"]
    referral_discount = result["referral_discount_amount"]
    balance_discount = result["balance_discount_amount"]
    volume_discount_percent = result["volume_discount_percent"]

    logger.debug("Сумма рассчитана", extra={
        "base": result["base_amount"],
        "volume_discount_percent": volume_discount_percent,
        "referral_discount": referral_discount,
        "balance_discount": balance_discount,
        "final_amount": final_amount,
    })

    import yookassa
    yookassa.Configuration.account_id = settings.yookassa_shop_id
    yookassa.Configuration.secret_key = settings.yookassa_secret_key

    idempotency_key = str(uuid.uuid4())
    logger.debug("Отправка запроса в YooKassa", extra={"idempotency_key": idempotency_key, "amount": final_amount, "payment_type": payment_type})

    try:
        # Формируем полный webhook URL для YooKassa
        webhook_url = f"{settings.webhook_base_url.rstrip('/')}{settings.webhook_path}"
        logger.debug("Webhook URL для YooKassa", extra={"webhook_url": webhook_url})

        yk_payment = yookassa.Payment.create(
            {
                "amount": {"value": f"{final_amount:.2f}", "currency": "RUB"},
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
                "notification_url": webhook_url,
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
        amount=final_amount,
        payment_type=payment_type,
        status="pending",
        number_of_months=body.number_of_months,
        discount_percent=volume_discount_percent,
        referral_discount=referral_discount,
        balance_discount=balance_discount,
    )
    logger.debug("PaymentModel создан", extra={"payment_dict": payment.to_dict()})

    try:
        await service_data.payments.save_data(pool, payment, payment_id=payment_id)
        logger.debug("Платёж сохранён в БД и кеше", extra={"payment_id": payment_id})
    except Exception as e:
        logger.error("Ошибка при сохранении платежа", extra={"error": str(e), "payment_id": payment_id})
        raise HTTPException(status_code=500, detail="Failed to save payment")

    logger.info("Платёж успешно создан", extra={"payment_id": payment_id, "tg_id": body.tg_id, "amount": final_amount})

    return PaymentCreateResponse(
        payment_id=payment_id,
        confirmation_url=confirmation_url,
        amount=final_amount,
        base_amount=result["base_amount"],
        volume_discount_percent=result["volume_discount_percent"],
        volume_discount_amount=result["volume_discount_amount"],
        referral_discount_amount=result["referral_discount_amount"],
        balance_discount_amount=result["balance_discount_amount"],
        final_amount=result["final_amount"],
        has_volume_discount=result["has_volume_discount"],
        has_referral_discount=result["has_referral_discount"],
        has_balance_discount=result["has_balance_discount"],
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
