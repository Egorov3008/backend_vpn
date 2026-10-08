"""Эндпоинты Telegram Mini App (`/api/v1/miniapp`).

В отличие от bot/web (доверяют `tg_id` из запроса при наличии X-Bot-Secret)
и public API (per-client ключ, тоже с `tg_id` из запроса), здесь клиент —
сам пользователь в браузере Telegram, поэтому:

- `POST /auth` проверяет подпись `Telegram.WebApp.initData` и выдаёт
  короткоживущий токен сессии (`app/core/miniapp_session.py`);
- все остальные эндпоинты берут `tg_id` только из этого токена
  (`verify_miniapp_session`) — параметра `tg_id` у них нет вовсе;
- владение ключом проверяется до вызова внутреннего хендлера, в том числе
  там, где внутренний хендлер этого не делает (`GET /keys/{email}`,
  `POST /payments` с `operation=renew_key`, `POST /keys/channel-bonus`);
- поля, которым внутренний API доверяет как сервисному вызову
  (`referral_discount`, `amount` в `PaymentCreateRequest`), здесь не
  принимаются — сумму считает только бэкенд.

Бизнес-логика не дублируется: вызываются те же handler-функции, что и во
внутренних роутерах `api/v1/*.py`.
"""
from typing import List, Literal, Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from api.v1.admin import admin_create_referral_link
from api.v1.auth import _notify_admins_telegram, get_user_repository
from api.v1.keys import (
    claim_channel_bonus,
    create_trial_key,
    delete_key,
    get_key,
    list_keys,
)
from api.v1.payments import (
    calculate_payment,
    create_payment,
    get_payment_history,
    get_payment_status,
)
from api.v1.tariffs import list_tariffs
from api.v1.users import get_user
from app.auth import MiniAppPrincipal, verify_miniapp_session
from app.core.miniapp_session import issue_session_token
from app.core.telegram import TelegramHashError, verify_webapp_init_data
from app.dependencies import get_cache, get_pool, get_service_data
from app.rate_limit import rate_limit
from app.repositories.users import UserRepository
from app.schemas.auth import TelegramAuthData
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
from app.services_auth import telegram_login
from config import settings
from logger import logger
from services.cache.key_manager import CacheKeyManager
from services.cache.service import CacheService
from services.core.data.service import ServiceDataModel

router = APIRouter(prefix="/miniapp", tags=["miniapp"])

_read_deps = [Depends(rate_limit("miniapp-read", times=120, seconds=60))]
_write_deps = [Depends(rate_limit("miniapp-write", times=20, seconds=60))]


class MiniAppAuthRequest(BaseModel):
    init_data: str = Field(..., min_length=1, max_length=4096, description="Telegram.WebApp.initData as is")


class MiniAppAuthResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    tg_id: int
    is_new: bool


class MiniAppQuoteRequest(BaseModel):
    tariff_id: int
    number_of_months: int = Field(1, ge=1, le=12)
    operation: Literal["create_key", "renew_key"] = "create_key"


class MiniAppPaymentRequest(MiniAppQuoteRequest):
    email: Optional[str] = Field(None, description="Key to renew; required for operation=renew_key")
    customer_email: Optional[str] = Field(None, description="E-mail for the fiscal receipt")


class MiniAppReferralLinkResponse(BaseModel):
    token: str


async def _require_own_key(email: str, tg_id: int, service_data: ServiceDataModel, pool) -> None:
    """404 и для чужого, и для несуществующего ключа — не раскрываем, что
    ключ с таким email существует у другого пользователя."""
    key = await service_data.keys.get_data(email)
    if not key:
        key = await service_data.data_service.keys.get(pool, email=email)
        if key:
            await service_data.cache_service.keys.set(CacheKeyManager.key(email), key)
    if not key or key.tg_id != tg_id:
        raise HTTPException(status_code=404, detail="Key not found")


@router.post(
    "/auth",
    response_model=MiniAppAuthResponse,
    dependencies=[Depends(rate_limit("miniapp-auth", times=20, seconds=60))],
)
async def miniapp_auth(
    body: MiniAppAuthRequest,
    user_repo: UserRepository = Depends(get_user_repository),
) -> MiniAppAuthResponse:
    """Обменивает подписанный Telegram `initData` на токен сессии; при первом
    входе регистрирует пользователя (как `POST /auth/telegram-login`)."""
    try:
        fields = verify_webapp_init_data(
            body.init_data,
            settings.bot_token,
            max_age_seconds=settings.miniapp_init_data_max_age_seconds,
        )
    except TelegramHashError as e:
        raise HTTPException(status_code=401, detail=str(e))

    tg_user = fields["user"]
    tg_id = tg_user["id"]

    existing = await user_repo.get_by_tg_id(tg_id)
    if existing is not None and getattr(existing, "is_blocked", False):
        raise HTTPException(status_code=403, detail="User is blocked")

    try:
        result = await telegram_login(
            TelegramAuthData(
                id=tg_id,
                first_name=tg_user.get("first_name"),
                last_name=tg_user.get("last_name"),
                username=tg_user.get("username"),
                photo_url=tg_user.get("photo_url"),
                auth_date=int(fields["auth_date"]),
                # Подпись уже проверена выше; telegram_login поле hash не читает.
                hash="",
            ),
            user_repo,
            notify_fn=_notify_admins_telegram,
        )
    except Exception:
        logger.error("miniapp auth: user registration failed", tg_id=tg_id, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")

    token, expires_in = issue_session_token(tg_id)
    return MiniAppAuthResponse(
        access_token=token,
        expires_in=expires_in,
        tg_id=tg_id,
        is_new=result["is_new"],
    )


@router.get("/me", response_model=UserResponse, dependencies=_read_deps)
async def miniapp_me(
    principal: MiniAppPrincipal = Depends(verify_miniapp_session),
    service_data: ServiceDataModel = Depends(get_service_data),
    pool=Depends(get_pool),
) -> UserResponse:
    return await get_user(tg_id=principal.tg_id, service_data=service_data, pool=pool)


@router.get("/tariffs", response_model=List[TariffResponse], dependencies=_read_deps)
async def miniapp_tariffs(
    principal: MiniAppPrincipal = Depends(verify_miniapp_session),
    service_data: ServiceDataModel = Depends(get_service_data),
    pool: asyncpg.Pool = Depends(get_pool),
):
    return await list_tariffs(service_data=service_data, pool=pool)


@router.get("/keys", response_model=List[KeyResponse], dependencies=_read_deps)
async def miniapp_list_keys(
    response: Response,
    limit: Optional[int] = Query(None, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    principal: MiniAppPrincipal = Depends(verify_miniapp_session),
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


@router.post("/keys/trial", response_model=KeyResponse, dependencies=_write_deps)
async def miniapp_create_trial_key(
    gift_token: Optional[str] = Query(None),
    principal: MiniAppPrincipal = Depends(verify_miniapp_session),
    pool: asyncpg.Pool = Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
    cache: CacheService = Depends(get_cache),
) -> KeyResponse:
    return await create_trial_key(
        tg_id=principal.tg_id,
        gift_token=gift_token,
        pool=pool,
        service_data=service_data,
        cache=cache,
    )


@router.post("/keys/channel-bonus", response_model=ChannelBonusResponse, dependencies=_write_deps)
async def miniapp_claim_channel_bonus(
    email: Optional[str] = Query(None),
    principal: MiniAppPrincipal = Depends(verify_miniapp_session),
    pool: asyncpg.Pool = Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
    cache: CacheService = Depends(get_cache),
):
    if email:
        await _require_own_key(email, principal.tg_id, service_data, pool)
    return await claim_channel_bonus(
        tg_id=principal.tg_id,
        email=email,
        pool=pool,
        service_data=service_data,
        cache=cache,
    )


@router.get("/keys/{email}", response_model=KeyDetailResponse, dependencies=_read_deps)
async def miniapp_get_key(
    email: str,
    principal: MiniAppPrincipal = Depends(verify_miniapp_session),
    service_data: ServiceDataModel = Depends(get_service_data),
    pool: asyncpg.Pool = Depends(get_pool),
) -> KeyDetailResponse:
    await _require_own_key(email, principal.tg_id, service_data, pool)
    return await get_key(email=email, service_data=service_data, pool=pool)


@router.delete("/keys/{email}", status_code=204, dependencies=_write_deps)
async def miniapp_delete_key(
    email: str,
    principal: MiniAppPrincipal = Depends(verify_miniapp_session),
    pool: asyncpg.Pool = Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
    cache: CacheService = Depends(get_cache),
):
    await _require_own_key(email, principal.tg_id, service_data, pool)
    await delete_key(
        email=email,
        tg_id=principal.tg_id,
        pool=pool,
        service_data=service_data,
        cache=cache,
    )
    return Response(status_code=204)


@router.post("/payments/quotes", response_model=PaymentCalculateResponse, dependencies=_read_deps)
async def miniapp_quote(
    body: MiniAppQuoteRequest,
    principal: MiniAppPrincipal = Depends(verify_miniapp_session),
    pool=Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
):
    return await calculate_payment(
        body=PaymentCalculateRequest(tg_id=principal.tg_id, **body.model_dump()),
        pool=pool,
        service_data=service_data,
    )


@router.post("/payments", response_model=PaymentCreateResponse, dependencies=_write_deps)
async def miniapp_create_payment(
    body: MiniAppPaymentRequest,
    principal: MiniAppPrincipal = Depends(verify_miniapp_session),
    pool=Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
    cache: CacheService = Depends(get_cache),
):
    if body.operation == "renew_key":
        if not body.email:
            raise HTTPException(status_code=422, detail="email required for renew_key operation")
        await _require_own_key(body.email, principal.tg_id, service_data, pool)

    tariff = await service_data.tariffs.get_data(body.tariff_id, conn=pool)
    if not tariff:
        raise HTTPException(status_code=404, detail="Tariff not found")
    if not tariff.amount or tariff.amount <= 0:
        # Бесплатные тарифы не оплачиваются; триал — через POST /keys/trial.
        raise HTTPException(status_code=400, detail="Tariff is not purchasable")

    # amount/referral_discount не передаются — сумму считает только бэкенд.
    return await create_payment(
        body=PaymentCreateRequest(
            tg_id=principal.tg_id,
            tariff_id=body.tariff_id,
            number_of_months=body.number_of_months,
            operation=body.operation,
            email=body.email if body.operation == "renew_key" else None,
            customer_email=body.customer_email,
        ),
        pool=pool,
        service_data=service_data,
        cache=cache,
    )


@router.get("/payments", response_model=List[PaymentHistoryItem], dependencies=_read_deps)
async def miniapp_payment_history(
    response: Response,
    limit: Optional[int] = Query(None, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    principal: MiniAppPrincipal = Depends(verify_miniapp_session),
    service_data: ServiceDataModel = Depends(get_service_data),
):
    return await get_payment_history(
        response=response,
        tg_id=principal.tg_id,
        limit=limit,
        offset=offset,
        service_data=service_data,
    )


@router.get("/payments/{payment_id}/status", response_model=PaymentStatusResponse, dependencies=_read_deps)
async def miniapp_payment_status(
    payment_id: str,
    principal: MiniAppPrincipal = Depends(verify_miniapp_session),
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


@router.post("/referral-link", response_model=MiniAppReferralLinkResponse, dependencies=_write_deps)
async def miniapp_referral_link(
    principal: MiniAppPrincipal = Depends(verify_miniapp_session),
    pool=Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
):
    """Возвращает реферальный токен пользователя, создавая его при первом вызове."""
    link = await admin_create_referral_link(tg_id=principal.tg_id, pool=pool, service_data=service_data)
    return MiniAppReferralLinkResponse(token=link["token"])
