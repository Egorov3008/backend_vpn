"""Эндпоинты Telegram Mini App (`/api/v1/miniapp`).

Клиент — сам пользователь в браузере Telegram:

- `POST /auth` проверяет подпись `Telegram.WebApp.initData` и выдаёт
  короткоживущий токен сессии (`app/core/miniapp_session.py`);
- остальные эндпоинты — общий кабинет (`api/v1/cabinet.py`), пользователь
  берётся только из токена (`verify_miniapp_session`).
"""
from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from api.v1.auth import _notify_admins_telegram, get_user_repository
from api.v1.cabinet import build_cabinet_router
from app.auth import verify_miniapp_session
from app.core.miniapp_session import issue_session_token
from app.core.telegram import TelegramHashError, verify_webapp_init_data
from app.rate_limit import rate_limit
from app.repositories.users import UserRepository
from app.schemas.auth import TelegramAuthData
from app.services_auth import telegram_login
from config import settings
from logger import logger

router = build_cabinet_router(
    prefix="/miniapp",
    tags=["miniapp"],
    principal_dep=verify_miniapp_session,
    rate_limit_name="miniapp",
    event_source="miniapp",
)


class MiniAppAuthRequest(BaseModel):
    init_data: str = Field(..., min_length=1, max_length=4096, description="Telegram.WebApp.initData as is")


class MiniAppAuthResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    tg_id: int
    is_new: bool


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
