"""Публичные (per-client API-ключ) эндпоинты для VPN-ключей.

Переиспользует те же handler-функции, что и внутренний api/v1/keys.py —
меняется только auth-зависимость (verify_api_client вместо verify_bot_secret),
поэтому бизнес-логика (3x-UI, кеш, БД) не дублируется.
"""
from typing import List

from fastapi import APIRouter, Depends

from api.v1.keys import (
    claim_channel_bonus,
    create_key,
    create_trial_key,
    delete_key,
    get_key,
    list_keys,
    renew_key,
)
from app.auth import verify_api_client
from app.rate_limit import rate_limit
from app.schemas.keys import ChannelBonusResponse, KeyDetailResponse, KeyResponse

router = APIRouter(prefix="/keys")

_read_deps = [
    Depends(verify_api_client(required_scopes=["keys:read"])),
    Depends(rate_limit("public-keys-read", times=60, seconds=60)),
]
_write_deps = [
    Depends(verify_api_client(required_scopes=["keys:write"])),
    Depends(rate_limit("public-keys-write", times=20, seconds=60)),
]

router.add_api_route("/", list_keys, methods=["GET"], response_model=List[KeyResponse], dependencies=_read_deps)
router.add_api_route(
    "/{email:path}", get_key, methods=["GET"], response_model=KeyDetailResponse, dependencies=_read_deps
)
router.add_api_route("/", create_key, methods=["POST"], response_model=KeyResponse, dependencies=_write_deps)
router.add_api_route(
    "/trial", create_trial_key, methods=["POST"], response_model=KeyResponse, dependencies=_write_deps
)
router.add_api_route("/{email}", delete_key, methods=["DELETE"], status_code=204, dependencies=_write_deps)
router.add_api_route(
    "/{email}", renew_key, methods=["PATCH"], response_model=KeyResponse, dependencies=_write_deps
)
router.add_api_route(
    "/channel-bonus",
    claim_channel_bonus,
    methods=["POST"],
    response_model=ChannelBonusResponse,
    dependencies=_write_deps,
)
