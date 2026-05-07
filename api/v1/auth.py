"""Authentication and registration endpoints."""
import hashlib
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.auth import verify_bot_secret
from app.core.telegram import verify_telegram_hash, TelegramHashError
from app.dependencies import get_pool, get_service_data
from app.repositories.users import UserRepository
from app.repositories.login_codes import LoginCodeRepository
from app.schemas.auth import (
    RegisterFromInviteRequest,
    RegisterFromInviteResponse,
    TelegramAuthData,
    TelegramLoginResponse,
)
from app.services_auth import register_from_invite, telegram_login
from config import settings, ADMIN_ID
from services.core.data.service import ServiceDataModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def get_user_repository(
    pool=Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
) -> UserRepository:
    return UserRepository(data_protocol=service_data.users, pool=pool)


def get_login_code_repository(
    pool=Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
) -> LoginCodeRepository:
    return LoginCodeRepository(data_protocol=service_data.login_codes, pool=pool)


async def _notify_admins_telegram(data: TelegramAuthData) -> None:
    name = data.first_name or ""
    username = f"@{data.username}" if data.username else "нет"
    text = (
        "👤 <b>Новая регистрация (Web)</b>\n\n"
        f"🆔 ID: <code>{data.id}</code>\n"
        f"👤 Имя: {name}\n"
        f"🔗 Username: {username}"
    )
    async with httpx.AsyncClient(timeout=5) as client:
        for admin_id in ADMIN_ID:
            try:
                await client.post(
                    f"https://api.telegram.org/bot{settings.bot_token}/sendMessage",
                    json={"chat_id": admin_id, "text": text, "parse_mode": "HTML"},
                )
            except Exception:
                pass


@router.post("/register-from-invite", response_model=RegisterFromInviteResponse, status_code=201)
async def register_from_invite_endpoint(
    request: RegisterFromInviteRequest,
    _: None = Depends(verify_bot_secret),
    pool=Depends(get_pool),
    user_repo: UserRepository = Depends(get_user_repository),
    login_code_repo: LoginCodeRepository = Depends(get_login_code_repository),
):
    """Register new user from web invite (bot only).

    This endpoint allows the bot to register new users with an invite token.
    Requires X-Bot-Secret header for authentication.

    Args:
        request: RegisterFromInviteRequest containing user data and invite token
        pool: asyncpg pool for database operations
        user_repo: UserRepository for user operations
        login_code_repo: LoginCodeRepository for login code operations

    Returns:
        RegisterFromInviteResponse with generated login code

    Raises:
        HTTPException: If invite token is invalid, user already exists, or database operation fails
    """
    try:
        result = await register_from_invite(request, user_repo, login_code_repo, pool)
        return result
    except ValueError as e:
        error_msg = str(e)
        if "already exists" in error_msg:
            raise HTTPException(status_code=409, detail=error_msg)
        raise HTTPException(status_code=400, detail=error_msg)
    except Exception as e:
        logger.error(f"register_from_invite failed for tg_id={request.tg_id}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error during registration")


@router.post("/telegram-login", response_model=TelegramLoginResponse)
async def telegram_login_endpoint(
    request: Request,
    body: TelegramAuthData,
    _: None = Depends(verify_bot_secret),
    user_repo: UserRepository = Depends(get_user_repository),
):
    try:
        verify_telegram_hash(body.dict(), settings.bot_token)
    except TelegramHashError as e:
        raise HTTPException(status_code=401, detail=str(e))

    try:
        result = await telegram_login(body, user_repo, notify_fn=_notify_admins_telegram)
        return result
    except Exception as e:
        logger.error("telegram_login failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
