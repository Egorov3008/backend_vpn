"""Кабинет пользователя: общий набор эндпоинтов для Mini App и веб-кабинета.

`build_cabinet_router` собирает один и тот же роутер для разных клиентов.
Клиенты отличаются только тем, как доказывают, кто пользователь
(`principal_dep`: Bearer-токен Mini App или cookie веб-сессии), и куда
YooKassa возвращает после оплаты. Новый клиент добавляется вызовом фабрики,
без копирования эндпоинтов.

Правила для всех клиентов:

- `tg_id` берётся только из сессии, параметра `tg_id` у эндпоинтов нет;
- владение ключом проверяется до вызова внутреннего хендлера, в том числе
  там, где внутренний хендлер этого не делает (`GET /keys/{email}`,
  `POST /payments` с `operation=renew_key`, `POST /keys/channel-bonus`);
- поля, которым внутренний API доверяет как сервисному вызову
  (`referral_discount`, `amount`), не принимаются — сумму считает бэкенд.

Бизнес-логика не дублируется: вызываются handler-функции внутренних
роутеров `api/v1/*.py`.
"""
from dataclasses import asdict
from typing import Awaitable, Callable, List, Literal, Optional, Sequence

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from api.v1.admin import admin_create_referral_link
from api.v1.keys import (
    claim_channel_bonus,
    create_trial_key,
    delete_key,
    get_key,
    list_keys,
)
from api.v1.payments import (
    calculate_payment,
    create_payment_for_user,
    get_payment_history,
    get_payment_status,
)
from api.v1.tariffs import list_tariffs
from api.v1.users import get_user
from app.auth import UserPrincipal
from app.dependencies import get_cache, get_event_tracker, get_pool, get_service_data
from app.rate_limit import rate_limit
from app.schemas.keys import ChannelBonusResponse, KeyDetailResponse, KeyResponse
from app.schemas.payments import (
    PaymentCalculateRequest,
    PaymentCalculateResponse,
    PaymentCreateRequest,
    PaymentCreateResponse,
    PaymentHistoryItem,
    PaymentStatusResponse,
)
from app.schemas.tariffs import TariffResponse
from app.schemas.users import UserResponse
from config import AVAILABLE_RATES_LIST
from models import Key, Tariff
from services.cache.key_manager import CacheKeyManager
from services.cache.service import CacheService
from services.core.data.service import ServiceDataModel
from services.core.referral.stats import ReferralStatsService
from services.user_events import EventTracker
from services.core.promotions.channel_subscription import (
    ChannelSubscriptionChecker,
    get_channel_subscription_checker,
)

PrincipalDependency = Callable[..., Awaitable[UserPrincipal]]


class CabinetQuoteRequest(BaseModel):
    tariff_id: int
    number_of_months: int = Field(1, ge=1, le=12)
    operation: Literal["create_key", "renew_key"] = "create_key"
    email: Optional[str] = Field(None, description="Key to renew; required for operation=renew_key")
    promo_code: Optional[str] = Field(None, max_length=64, description="Промокод, если пользователь его ввёл")


class CabinetPaymentRequest(CabinetQuoteRequest):
    customer_email: Optional[str] = Field(None, description="E-mail for the fiscal receipt")


class CabinetTariffResponse(TariffResponse):
    # True — тариф снят с продажи (нет в AVAILABLE_RATES). Ключ на нём
    # продлевается только по нему; новый ключ на нём не купить.
    renew_only: bool = False


class CabinetReferralLinkResponse(BaseModel):
    token: str
    # Ссылка-приглашение на сайт; None, если не задан WEB_PUBLIC_URL.
    share_url: Optional[str] = None


class CabinetReferralStatsResponse(BaseModel):
    invited_count: int
    paid_count: int
    earned_total: float
    balance: float
    bonus_percent: int
    friend_discount_percent: int


def available_rates() -> List[int]:
    """Тарифы, которые можно купить из кабинета (`AVAILABLE_RATES`)."""
    return AVAILABLE_RATES_LIST


def allowed_tariff_ids(key: Optional[Key] = None, key_tariff: Optional[Tariff] = None) -> List[int]:
    """Какими тарифами можно оплатить из кабинета.

    Новый ключ, пробный ключ и ключ на тарифе из `AVAILABLE_RATES` — любой
    тариф из `AVAILABLE_RATES`. Ключ на платном тарифе, снятом с продажи,
    продлевается только по своему тарифу, без выбора.
    """
    if key is not None and key_tariff is not None and is_off_sale_paid(key_tariff):
        return [key_tariff.id]
    return list(available_rates())


def is_off_sale_paid(tariff: Tariff) -> bool:
    """Платный тариф, которого нет в `AVAILABLE_RATES`."""
    return tariff.id not in available_rates() and bool(tariff.amount) and tariff.amount > 0


async def require_own_key(email: str, tg_id: int, service_data: ServiceDataModel, pool) -> Key:
    """404 и для чужого, и для несуществующего ключа — не раскрываем, что
    ключ с таким email существует у другого пользователя."""
    key = await service_data.keys.get_data(email)
    if not key:
        key = await service_data.data_service.keys.get(pool, email=email)
        if key:
            await service_data.cache_service.keys.set(CacheKeyManager.key(email), key)
    if not key or key.tg_id != tg_id:
        raise HTTPException(status_code=404, detail="Key not found")
    return key


async def require_payable_tariff(
    body: CabinetQuoteRequest,
    tg_id: int,
    service_data: ServiceDataModel,
    pool,
    *,
    email_required: bool,
) -> None:
    """Проверки перед расчётом и оплатой: свой ключ для продления, тариф
    разрешён для этой операции и платный. Одно правило для quote и оплаты,
    чтобы сумма на экране и в счёте считалась по одному и тому же тарифу.

    Для quote email необязателен (старые клиенты его не шлют): без ключа
    доступны только тарифы из `AVAILABLE_RATES`."""
    key = None
    if body.operation == "renew_key":
        if body.email:
            key = await require_own_key(body.email, tg_id, service_data, pool)
        elif email_required:
            raise HTTPException(status_code=422, detail="email required for renew_key operation")

    key_tariff = None
    if key is not None and key.tariff_id is not None:
        key_tariff = await service_data.tariffs.get_data(key.tariff_id, conn=pool)
    tariff = await service_data.tariffs.get_data(body.tariff_id, conn=pool)
    if not tariff or body.tariff_id not in allowed_tariff_ids(key, key_tariff):
        raise HTTPException(status_code=404, detail="Tariff not found")
    if not tariff.amount or tariff.amount <= 0:
        # Бесплатные тарифы не оплачиваются; триал — через POST /keys/trial.
        raise HTTPException(status_code=400, detail="Tariff is not purchasable")


def build_cabinet_router(
    *,
    prefix: str,
    tags: Sequence[str],
    principal_dep: PrincipalDependency,
    rate_limit_name: str,
    event_source: str,
    return_url: Callable[[], Optional[str]] = lambda: None,
) -> APIRouter:
    """Роутер кабинета.

    Args:
        prefix: путь клиента, например ``/miniapp`` или ``/web``.
        principal_dep: зависимость, возвращающая пользователя из сессии.
        rate_limit_name: префикс корзин rate limit (``<name>-read``/``<name>-write``).
        event_source: ``source`` событий журнала действий (``miniapp``/``web``).
        return_url: куда YooKassa вернёт после оплаты; ``None`` — в бота.
    """
    router = APIRouter(prefix=prefix, tags=list(tags))
    read_deps = [Depends(rate_limit(f"{rate_limit_name}-read", times=120, seconds=60))]
    write_deps = [Depends(rate_limit(f"{rate_limit_name}-write", times=20, seconds=60))]

    @router.get("/me", response_model=UserResponse, dependencies=read_deps)
    async def cabinet_me(
        principal: UserPrincipal = Depends(principal_dep),
        service_data: ServiceDataModel = Depends(get_service_data),
        pool=Depends(get_pool),
        events: EventTracker = Depends(get_event_tracker),
    ) -> UserResponse:
        user = await get_user(tg_id=principal.tg_id, service_data=service_data, pool=pool)
        # Клиенты запрашивают /me один раз при открытии — это и есть «открыл приложение».
        await events.track("app_opened", source=event_source, tg_id=principal.tg_id)
        return user

    @router.get("/tariffs", response_model=List[CabinetTariffResponse], dependencies=read_deps)
    async def cabinet_tariffs(
        principal: UserPrincipal = Depends(principal_dep),
        service_data: ServiceDataModel = Depends(get_service_data),
        pool: asyncpg.Pool = Depends(get_pool),
    ):
        """Тарифы в продаже и, с `renew_only`, снятые с продажи платные тарифы
        ключей пользователя: ключ на таком тарифе продлевается только по нему."""
        tariffs = await list_tariffs(service_data=service_data, pool=pool)
        allowed = set(available_rates())
        keys = await service_data.data_service.keys.filter(pool, tg_id=principal.tg_id)
        own_tariffs = {k.tariff_id for k in keys if k.tariff_id is not None}
        result = []
        for t in tariffs:
            if t.id in allowed:
                result.append(CabinetTariffResponse(**t.model_dump()))
            elif t.id in own_tariffs and is_off_sale_paid(t):
                result.append(CabinetTariffResponse(**t.model_dump(), renew_only=True))
        return result

    @router.get("/keys", response_model=List[KeyResponse], dependencies=read_deps)
    async def cabinet_list_keys(
        response: Response,
        limit: Optional[int] = Query(None, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        principal: UserPrincipal = Depends(principal_dep),
        service_data: ServiceDataModel = Depends(get_service_data),
        pool: asyncpg.Pool = Depends(get_pool),
    ) -> List[KeyResponse]:
        return await list_keys(
            response=response,
            tg_id=principal.tg_id,
            limit=limit,
            offset=offset,
            service_data=service_data,
            pool=pool,
        )

    @router.post("/keys/trial", response_model=KeyResponse, dependencies=write_deps)
    async def cabinet_create_trial_key(
        gift_token: Optional[str] = Query(None),
        principal: UserPrincipal = Depends(principal_dep),
        pool: asyncpg.Pool = Depends(get_pool),
        service_data: ServiceDataModel = Depends(get_service_data),
        cache: CacheService = Depends(get_cache),
        events: EventTracker = Depends(get_event_tracker),
    ) -> KeyResponse:
        key = await create_trial_key(
            tg_id=principal.tg_id,
            gift_token=gift_token,
            pool=pool,
            service_data=service_data,
            cache=cache,
        )
        await events.track("trial_created", source=event_source, tg_id=principal.tg_id)
        return key

    @router.post("/keys/channel-bonus", response_model=ChannelBonusResponse, dependencies=write_deps)
    async def cabinet_claim_channel_bonus(
        email: Optional[str] = Query(None),
        principal: UserPrincipal = Depends(principal_dep),
        pool: asyncpg.Pool = Depends(get_pool),
        service_data: ServiceDataModel = Depends(get_service_data),
        cache: CacheService = Depends(get_cache),
        subscription: ChannelSubscriptionChecker = Depends(get_channel_subscription_checker),
    ):
        if email:
            await require_own_key(email, principal.tg_id, service_data, pool)
        # Бот проверяет подписку сам, а кабинет ходит сюда напрямую — без этой
        # проверки бонус получал любой, не подписываясь на канал.
        if not subscription.configured:
            raise HTTPException(status_code=503, detail="Channel bonus is not configured")
        subscribed = await subscription.is_subscribed(principal.tg_id)
        if subscribed is None:
            raise HTTPException(status_code=503, detail="Could not check channel subscription")
        if not subscribed:
            return ChannelBonusResponse(status="not_subscribed", channel_url=subscription.channel_url)
        return await claim_channel_bonus(
            tg_id=principal.tg_id,
            email=email,
            pool=pool,
            service_data=service_data,
            cache=cache,
        )

    @router.get("/keys/{email}", response_model=KeyDetailResponse, dependencies=read_deps)
    async def cabinet_get_key(
        email: str,
        principal: UserPrincipal = Depends(principal_dep),
        service_data: ServiceDataModel = Depends(get_service_data),
        pool: asyncpg.Pool = Depends(get_pool),
    ) -> KeyDetailResponse:
        await require_own_key(email, principal.tg_id, service_data, pool)
        return await get_key(email=email, service_data=service_data, pool=pool)

    @router.delete("/keys/{email}", status_code=204, dependencies=write_deps)
    async def cabinet_delete_key(
        email: str,
        principal: UserPrincipal = Depends(principal_dep),
        pool: asyncpg.Pool = Depends(get_pool),
        service_data: ServiceDataModel = Depends(get_service_data),
        cache: CacheService = Depends(get_cache),
        events: EventTracker = Depends(get_event_tracker),
    ):
        await require_own_key(email, principal.tg_id, service_data, pool)
        await delete_key(
            email=email,
            tg_id=principal.tg_id,
            pool=pool,
            service_data=service_data,
            cache=cache,
        )
        await events.track("key_deleted", source=event_source, tg_id=principal.tg_id)
        return Response(status_code=204)

    @router.post("/payments/quotes", response_model=PaymentCalculateResponse, dependencies=read_deps)
    async def cabinet_quote(
        body: CabinetQuoteRequest,
        principal: UserPrincipal = Depends(principal_dep),
        pool=Depends(get_pool),
        service_data: ServiceDataModel = Depends(get_service_data),
    ):
        await require_payable_tariff(body, principal.tg_id, service_data, pool, email_required=False)
        return await calculate_payment(
            body=PaymentCalculateRequest(
                tg_id=principal.tg_id,
                tariff_id=body.tariff_id,
                number_of_months=body.number_of_months,
                operation=body.operation,
                promo_code=body.promo_code,
            ),
            pool=pool,
            service_data=service_data,
        )

    @router.post("/payments", response_model=PaymentCreateResponse, dependencies=write_deps)
    async def cabinet_create_payment(
        body: CabinetPaymentRequest,
        principal: UserPrincipal = Depends(principal_dep),
        pool=Depends(get_pool),
        service_data: ServiceDataModel = Depends(get_service_data),
        cache: CacheService = Depends(get_cache),
        events: EventTracker = Depends(get_event_tracker),
    ):
        await require_payable_tariff(body, principal.tg_id, service_data, pool, email_required=True)

        # amount/referral_discount не передаются — сумму считает только бэкенд.
        payment = await create_payment_for_user(
            PaymentCreateRequest(
                tg_id=principal.tg_id,
                tariff_id=body.tariff_id,
                number_of_months=body.number_of_months,
                operation=body.operation,
                email=body.email if body.operation == "renew_key" else None,
                customer_email=body.customer_email,
                promo_code=body.promo_code,
            ),
            pool,
            service_data,
            cache,
            return_url=return_url(),
        )
        await events.track_payment_created(
            source=event_source,
            tg_id=principal.tg_id,
            operation=body.operation,
            tariff_id=body.tariff_id,
            months=body.number_of_months,
            amount=payment.amount,
            promo_code=body.promo_code if payment.promo_status == "applied" else None,
        )
        return payment

    @router.get("/payments", response_model=List[PaymentHistoryItem], dependencies=read_deps)
    async def cabinet_payment_history(
        response: Response,
        limit: Optional[int] = Query(None, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        principal: UserPrincipal = Depends(principal_dep),
        service_data: ServiceDataModel = Depends(get_service_data),
    ):
        return await get_payment_history(
            response=response,
            tg_id=principal.tg_id,
            limit=limit,
            offset=offset,
            service_data=service_data,
        )

    @router.get("/payments/{payment_id}/status", response_model=PaymentStatusResponse, dependencies=read_deps)
    async def cabinet_payment_status(
        payment_id: str,
        principal: UserPrincipal = Depends(principal_dep),
        service_data: ServiceDataModel = Depends(get_service_data),
        pool=Depends(get_pool),
        cache: CacheService = Depends(get_cache),
    ):
        return await get_payment_status(
            payment_id=payment_id,
            tg_id=principal.tg_id,
            service_data=service_data,
            pool=pool,
            cache=cache,
        )

    @router.post("/referral-link", response_model=CabinetReferralLinkResponse, dependencies=write_deps)
    async def cabinet_referral_link(
        principal: UserPrincipal = Depends(principal_dep),
        pool=Depends(get_pool),
        service_data: ServiceDataModel = Depends(get_service_data),
    ):
        """Возвращает реферальный токен пользователя, создавая его при первом вызове."""
        link = await admin_create_referral_link(tg_id=principal.tg_id, pool=pool, service_data=service_data)
        return CabinetReferralLinkResponse(token=link["token"], share_url=link["share_url"])

    @router.get("/referral-stats", response_model=CabinetReferralStatsResponse, dependencies=read_deps)
    async def cabinet_referral_stats(
        principal: UserPrincipal = Depends(principal_dep),
        pool=Depends(get_pool),
    ):
        """Сколько друзей пришло и оплатило, сколько начислено и сколько на балансе."""
        stats = await ReferralStatsService(pool).for_user(principal.tg_id)
        return CabinetReferralStatsResponse(**asdict(stats))

    return router
