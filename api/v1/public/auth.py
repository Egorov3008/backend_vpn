"""Публичные (per-client API-ключ) эндпоинты для регистрации/логина.

Переиспользует те же handler-функции, что и внутренний api/v1/auth.py —
меняется только auth-зависимость.
"""
from fastapi import APIRouter, Depends

from api.v1.auth import register_from_invite_endpoint, telegram_login_endpoint
from app.auth import verify_api_client
from app.rate_limit import rate_limit
from app.schemas.auth import RegisterFromInviteResponse, TelegramLoginResponse

router = APIRouter(prefix="/auth")

_deps = [
    Depends(verify_api_client(required_scopes=["auth:register"])),
    Depends(rate_limit("public-auth", times=20, seconds=60)),
]

router.add_api_route(
    "/register-from-invite",
    register_from_invite_endpoint,
    methods=["POST"],
    response_model=RegisterFromInviteResponse,
    status_code=201,
    dependencies=_deps,
)
router.add_api_route(
    "/telegram-login",
    telegram_login_endpoint,
    methods=["POST"],
    response_model=TelegramLoginResponse,
    dependencies=_deps,
)
