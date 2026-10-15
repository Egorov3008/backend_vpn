"""Веб-кабинет (`/api/v1/web`) для пользователей без доступа к Telegram.

Поток:

1. Анонимный старт: ``POST /web/anonymous/key`` выдаёт 24-часовой ключ через
   механику лендинга (`api/v1/landing.py`), аккаунт живёт в cookie
   ``tg_landing_id``. ``GET /web/anonymous/state`` — его состояние.
2. Вход (и привязка анонимного ключа): ``POST /web/auth/bot-login`` даёт
   ссылку ``t.me/<бот>?start=weblogin_<token>``. Пользователь подтверждает
   вход в боте, бот вызывает ``POST /auth/web-login/{token}/confirm``
   (``bot_router``, только с секретами бота). Сайт опрашивает
   ``POST /web/auth/bot-login/complete`` и получает cookie ``web_session``.
3. Кабинет: те же эндпоинты, что у Mini App (`api/v1/cabinet.py`),
   пользователь — из cookie ``web_session``.

Секретов у веб-клиента нет: nginx веба только проксирует ``/api/v1/web/``.
"""
from typing import Literal, Optional

import asyncpg
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel

from api.v1 import landing
from api.v1.cabinet import build_cabinet_router
from app.auth import (
    WEB_SESSION_COOKIE,
    require_web_csrf_header,
    verify_bot_secret,
    verify_landing_internal_secret,
    verify_web_session,
)
from app.core.miniapp_session import web_sessions
from app.dependencies import get_cache, get_pool, get_service_data
from app.rate_limit import rate_limit
from config import settings
from logger import logger
from services.cache.service import CacheService
from services.core.data.service import ServiceDataModel
from services.web_auth.service import (
    DEEP_LINK_PREFIX,
    ConfirmStatus,
    PollStatus,
    WebLoginService,
)
from services.web_auth.store import PostgresWebLoginTokenStore

WEB_LOGIN_POLL_COOKIE = "web_login_poll"
_WEB_PATH = "/api/v1/web"
_WEB_AUTH_PATH = "/api/v1/web/auth"


def web_payment_return_url() -> Optional[str]:
    """Куда YooKassa вернёт после оплаты из веба. Пусто — в бота."""
    base = settings.web_public_url.strip().rstrip("/")
    return f"{base}/pay/result" if base else None


router = build_cabinet_router(
    prefix="/web",
    tags=["web"],
    principal_dep=verify_web_session,
    rate_limit_name="web",
    return_url=web_payment_return_url,
)

# Вызывается только ботом: X-Bot-Secret + X-Landing-Internal-Secret.
# Путь /api/v1/auth/* наружу не проксируется ни одним nginx.
bot_router = APIRouter(
    prefix="/auth/web-login",
    tags=["auth"],
    dependencies=[Depends(verify_bot_secret), Depends(verify_landing_internal_secret)],
)


def get_web_login_service(pool: asyncpg.Pool = Depends(get_pool)) -> WebLoginService:
    return WebLoginService(
        PostgresWebLoginTokenStore(pool),
        ttl_seconds=settings.web_login_token_ttl_seconds,
    )


def _set_cookie(response: Response, name: str, value: str, *, max_age: int, path: str) -> None:
    response.set_cookie(
        key=name,
        value=value,
        max_age=max_age,
        httponly=True,
        secure=settings.web_cookie_secure,
        samesite="strict",
        path=path,
    )


def _delete_cookie(response: Response, name: str, *, path: str) -> None:
    response.delete_cookie(
        key=name, path=path, httponly=True, secure=settings.web_cookie_secure, samesite="strict"
    )


# --- Анонимный старт ---------------------------------------------------------


@router.get(
    "/anonymous/state",
    response_model=landing.LandingStateResponse,
    dependencies=[Depends(rate_limit("web-anon-state", times=60, seconds=60))],
)
async def web_anonymous_state(
    tg_landing_id: Optional[str] = Cookie(None),
    pool: asyncpg.Pool = Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
) -> landing.LandingStateResponse:
    return await landing.get_state(tg_landing_id=tg_landing_id, pool=pool, service_data=service_data)


@router.post(
    "/anonymous/key",
    response_model=landing.QuickKeyResponse,
    dependencies=[
        Depends(require_web_csrf_header),
        Depends(rate_limit("web-anon-key", times=3, seconds=3600)),
    ],
)
async def web_anonymous_key(
    response: Response,
    tg_landing_id: Optional[str] = Cookie(None),
    pool: asyncpg.Pool = Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
    cache: CacheService = Depends(get_cache),
) -> landing.QuickKeyResponse:
    """24-часовой ключ на 1 устройство без регистрации (как на лендинге).

    Второй ключ на тот же браузер, пока первый жив, не выдаётся."""
    state = await landing.get_state(tg_landing_id=tg_landing_id, pool=pool, service_data=service_data)
    if state.state in ("active", "expiring"):
        raise HTTPException(status_code=409, detail="Anonymous key already issued")
    return await landing.create_quick_key(
        response=response, pool=pool, service_data=service_data, cache=cache
    )


# --- Вход через бота ---------------------------------------------------------


class BotLoginStartResponse(BaseModel):
    deep_link: str
    expires_in: int


class BotLoginCompleteResponse(BaseModel):
    status: Literal["pending", "confirmed", "expired"]
    tg_id: Optional[int] = None


@router.post(
    "/auth/bot-login",
    response_model=BotLoginStartResponse,
    dependencies=[
        Depends(require_web_csrf_header),
        Depends(rate_limit("web-bot-login", times=10, seconds=600)),
    ],
)
async def web_bot_login_start(
    response: Response,
    tg_landing_id: Optional[str] = Cookie(None),
    service: WebLoginService = Depends(get_web_login_service),
) -> BotLoginStartResponse:
    """Начать вход: ссылка в бота + секрет опроса в cookie этого браузера.

    Если в браузере есть анонимный ключ, его landing_uid уходит в токен: бот
    после подтверждения привяжет этот ключ к аккаунту."""
    started = await service.start(landing_uid=landing.landing_uid_from_cookie(tg_landing_id))
    _set_cookie(
        response,
        WEB_LOGIN_POLL_COOKIE,
        started.poll_secret,
        max_age=started.expires_in,
        path=_WEB_AUTH_PATH,
    )
    bot_url = await landing._bot_url()
    return BotLoginStartResponse(
        deep_link=f"{bot_url}?start={DEEP_LINK_PREFIX}{started.token}",
        expires_in=started.expires_in,
    )


@router.post(
    "/auth/bot-login/complete",
    response_model=BotLoginCompleteResponse,
    dependencies=[
        Depends(require_web_csrf_header),
        Depends(rate_limit("web-bot-login-poll", times=120, seconds=60)),
    ],
)
async def web_bot_login_complete(
    response: Response,
    web_login_poll: Optional[str] = Cookie(None),
    service: WebLoginService = Depends(get_web_login_service),
) -> BotLoginCompleteResponse:
    """Опрос: после подтверждения в боте ставит cookie ``web_session`` (один раз)."""
    result = await service.complete(web_login_poll)
    if result.status == PollStatus.CONFIRMED:
        token, ttl = web_sessions.issue(result.tg_id)
        _set_cookie(response, WEB_SESSION_COOKIE, token, max_age=ttl, path=_WEB_PATH)
        _delete_cookie(response, WEB_LOGIN_POLL_COOKIE, path=_WEB_AUTH_PATH)
        logger.info("Web login completed", tg_id=result.tg_id)
        return BotLoginCompleteResponse(status="confirmed", tg_id=result.tg_id)
    if result.status == PollStatus.EXPIRED:
        _delete_cookie(response, WEB_LOGIN_POLL_COOKIE, path=_WEB_AUTH_PATH)
    return BotLoginCompleteResponse(status=result.status.value)


@router.post("/auth/logout", status_code=204, dependencies=[Depends(require_web_csrf_header)])
async def web_logout(response: Response) -> Response:
    _delete_cookie(response, WEB_SESSION_COOKIE, path=_WEB_PATH)
    response.status_code = 204
    return response


# --- Подтверждение ботом -----------------------------------------------------


class WebLoginConfirmRequest(BaseModel):
    tg_id: int


class WebLoginConfirmResponse(BaseModel):
    status: Literal["confirmed", "already_confirmed", "expired"]
    # Анонимный ключ браузера, начавшего вход. Бот привязывает его тем же
    # путём, что и /start landing_<uid> (claim для нового пользователя,
    # mark-converted для существующего).
    landing_uid: Optional[str] = None


@bot_router.post("/{token}/confirm", response_model=WebLoginConfirmResponse)
async def web_login_confirm(
    token: str,
    body: WebLoginConfirmRequest,
    service: WebLoginService = Depends(get_web_login_service),
) -> WebLoginConfirmResponse:
    result = await service.confirm(token, body.tg_id)
    if result.status == ConfirmStatus.CONFIRMED:
        logger.info("Web login confirmed in bot", tg_id=body.tg_id, has_landing=bool(result.landing_uid))
    return WebLoginConfirmResponse(status=result.status.value, landing_uid=result.landing_uid)
