"""Продление ключа по прямой ссылке без входа: ``/api/v1/web/renew/{email}``.

Ссылка ``<WEB_PUBLIC_URL>/renew/<email>`` ставится в настройки подписки 3x-ui
(Profile page с подстановкой ``{{EMAIL}}``), и клиент VPN (Happ, INCY)
показывает её кнопкой у каждого ключа. Открывается во внешнем браузере, где нет
ни initData Telegram, ни веб-сессии, поэтому вход не нужен.

Почему это безопасно: email ключа — это его ``subId``. Кто знает email, уже
может скачать подписку ключа, поэтому страница продления ничего нового не
раскрывает. Платёж записывается на владельца ключа и продлевает только этот
ключ, дальше работает обычный webhook YooKassa.

Отличия от кабинета:

- операция всегда ``renew_key`` этого ключа; тариф — по правилам продления
  кабинета (``allowed_tariff_ids``);
- баланс владельца не списывается и не виден (платить может не владелец);
  остальные скидки (акция, 10% приглашённому, срок, промокод) как в кабинете;
- ответы не содержат ``tg_id`` и других данных владельца;
- гостевые ключи лендинга (псевдо ``tg_id`` < 0) не продлеваются: им нужен вход.
"""
from typing import List, Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.v1.cabinet import (
    CabinetQuoteRequest,
    CabinetTariffResponse,
    allowed_tariff_ids,
    is_off_sale_paid,
    require_payable_tariff,
)
from api.v1.payments import create_payment_for_user, get_payment_status, quote_for_user
from app.dependencies import get_cache, get_pool, get_service_data
from app.rate_limit import rate_limit
from app.schemas.tariffs import TariffResponse
from app.schemas.payments import (
    PaymentCalculateRequest,
    PaymentCalculateResponse,
    PaymentCreateRequest,
    PaymentCreateResponse,
)
from config import settings
from models import Key
from services.cache.key_manager import CacheKeyManager
from services.cache.service import CacheService
from services.core.data.service import ServiceDataModel

router = APIRouter(prefix="/web/renew", tags=["web-renew"])

_read_deps = [Depends(rate_limit("web-renew-read", times=60, seconds=60))]
_write_deps = [Depends(rate_limit("web-renew-write", times=10, seconds=600))]


class RenewKeyResponse(BaseModel):
    email: str
    expiry_time: int
    # Тариф ключа (может быть снят с продажи); None — тариф не найден.
    tariff_id: Optional[int] = None
    name_tariff: Optional[str] = None
    # Чем можно продлить: тарифы из AVAILABLE_RATES или только свой (renew_only).
    tariffs: List[CabinetTariffResponse]


class RenewQuoteRequest(BaseModel):
    tariff_id: int
    number_of_months: int = Field(1, ge=1, le=12)
    promo_code: Optional[str] = Field(None, max_length=64)


class RenewPaymentRequest(RenewQuoteRequest):
    customer_email: Optional[str] = Field(None, description="E-mail for the fiscal receipt")


class RenewPaymentStatusResponse(BaseModel):
    payment_id: str
    status: str
    processed: bool


def renew_return_url(email: str) -> Optional[str]:
    """Куда YooKassa вернёт после оплаты по ссылке. Пусто — в бота."""
    base = settings.web_public_url.strip().rstrip("/")
    return f"{base}/renew/{email}/result" if base else None


async def _renewable_key(email: str, service_data: ServiceDataModel, pool) -> Key:
    """Ключ, который можно продлить по ссылке. 404 и для несуществующего,
    и для гостевого ключа лендинга — не раскрываем разницу."""
    key = await service_data.keys.get_data(email)
    if not key:
        key = await service_data.data_service.keys.get(pool, email=email)
        if key:
            await service_data.cache_service.keys.set(CacheKeyManager.key(email), key)
    if not key or not key.tg_id or key.tg_id <= 0:
        raise HTTPException(status_code=404, detail="Key not found")
    return key


async def _check_tariff(key: Key, body: RenewQuoteRequest, service_data: ServiceDataModel, pool) -> None:
    await require_payable_tariff(
        CabinetQuoteRequest(
            tariff_id=body.tariff_id,
            number_of_months=body.number_of_months,
            operation="renew_key",
            email=key.email,
        ),
        key.tg_id,
        service_data,
        pool,
        email_required=True,
    )


@router.get("/{email}", response_model=RenewKeyResponse, dependencies=_read_deps)
async def renew_key_info(
    email: str,
    service_data: ServiceDataModel = Depends(get_service_data),
    pool: asyncpg.Pool = Depends(get_pool),
) -> RenewKeyResponse:
    key = await _renewable_key(email, service_data, pool)
    key_tariff = None
    if key.tariff_id is not None:
        key_tariff = await service_data.tariffs.get_data(key.tariff_id, conn=pool)
    allowed = allowed_tariff_ids(key, key_tariff)
    tariffs = []
    for tariff_id in allowed:
        tariff = key_tariff if key_tariff and key_tariff.id == tariff_id else (
            await service_data.tariffs.get_data(tariff_id, conn=pool)
        )
        if tariff and tariff.amount and tariff.amount > 0:
            tariffs.append(CabinetTariffResponse(
                **TariffResponse.from_tariff(tariff).model_dump(), renew_only=is_off_sale_paid(tariff)
            ))
    return RenewKeyResponse(
        email=key.email,
        expiry_time=key.expiry_time,
        tariff_id=key_tariff.id if key_tariff else None,
        name_tariff=key_tariff.name_tariff if key_tariff else None,
        tariffs=tariffs,
    )


@router.post("/{email}/quotes", response_model=PaymentCalculateResponse, dependencies=_read_deps)
async def renew_key_quote(
    email: str,
    body: RenewQuoteRequest,
    service_data: ServiceDataModel = Depends(get_service_data),
    pool: asyncpg.Pool = Depends(get_pool),
) -> PaymentCalculateResponse:
    key = await _renewable_key(email, service_data, pool)
    await _check_tariff(key, body, service_data, pool)
    return await quote_for_user(
        PaymentCalculateRequest(
            tg_id=key.tg_id,
            tariff_id=body.tariff_id,
            number_of_months=body.number_of_months,
            operation="renew_key",
            promo_code=body.promo_code,
        ),
        pool,
        service_data,
        use_balance=False,
    )


@router.post("/{email}/payments", response_model=PaymentCreateResponse, dependencies=_write_deps)
async def renew_key_payment(
    email: str,
    body: RenewPaymentRequest,
    service_data: ServiceDataModel = Depends(get_service_data),
    pool: asyncpg.Pool = Depends(get_pool),
    cache: CacheService = Depends(get_cache),
) -> PaymentCreateResponse:
    key = await _renewable_key(email, service_data, pool)
    await _check_tariff(key, body, service_data, pool)
    # amount/referral_discount не передаются — сумму считает только бэкенд.
    return await create_payment_for_user(
        PaymentCreateRequest(
            tg_id=key.tg_id,
            tariff_id=body.tariff_id,
            number_of_months=body.number_of_months,
            operation="renew_key",
            email=key.email,
            customer_email=body.customer_email,
            promo_code=body.promo_code,
        ),
        pool,
        service_data,
        cache,
        return_url=renew_return_url(key.email),
        use_balance=False,
    )


@router.get(
    "/{email}/payments/{payment_id}/status",
    response_model=RenewPaymentStatusResponse,
    dependencies=_read_deps,
)
async def renew_key_payment_status(
    email: str,
    payment_id: str,
    service_data: ServiceDataModel = Depends(get_service_data),
    pool: asyncpg.Pool = Depends(get_pool),
    cache: CacheService = Depends(get_cache),
) -> RenewPaymentStatusResponse:
    """Статус только платежа за продление этого ключа: другие платежи
    владельца по этой ссылке не видны."""
    key = await _renewable_key(email, service_data, pool)
    payment = await service_data.payments.get_data(payment_id)
    if not payment or payment.tg_id != key.tg_id or payment.payment_type != f"renew_key|{key.email}":
        raise HTTPException(status_code=404, detail="Payment not found")
    status = await get_payment_status(
        payment_id=payment_id, tg_id=key.tg_id, service_data=service_data, pool=pool, cache=cache
    )
    return RenewPaymentStatusResponse(
        payment_id=status.payment_id, status=status.status, processed=status.processed
    )
