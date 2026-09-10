"""Публичные (per-client API-ключ) эндпоинты для пользователей.

Переиспользует те же handler-функции, что и внутренний api/v1/users.py —
меняется только auth-зависимость.
"""
from fastapi import APIRouter, Depends

from api.v1.users import get_user, register_user, update_user
from app.auth import verify_api_client
from app.rate_limit import rate_limit
from app.schemas.users import UserResponse

router = APIRouter(prefix="/users")

_read_deps = [
    Depends(verify_api_client(required_scopes=["users:read"])),
    Depends(rate_limit("public-users-read", times=60, seconds=60)),
]
_write_deps = [
    Depends(verify_api_client(required_scopes=["users:write"])),
    Depends(rate_limit("public-users-write", times=20, seconds=60)),
]

router.add_api_route("/{tg_id}", get_user, methods=["GET"], response_model=UserResponse, dependencies=_read_deps)
router.add_api_route("/register", register_user, methods=["POST"], dependencies=_write_deps)
router.add_api_route(
    "/{tg_id}", update_user, methods=["PATCH"], response_model=UserResponse, dependencies=_write_deps
)
