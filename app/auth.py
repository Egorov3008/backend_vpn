from dataclasses import dataclass
from typing import List, Optional

from fastapi import Depends, Header, HTTPException, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from app.dependencies import get_pool
from config import settings
from models.api_clients.api_client import ApiClient
from services.api_clients.service import ApiClientService

# APIKeyHeader (вместо голого Header) — единственная причина: FastAPI
# автоматически регистрирует это как OpenAPI securityScheme (замочек в Swagger
# UI + `security` на каждом эндпоинте), а не просто как header-параметр.
# Сама проверка значения ниже не меняется.
bot_secret_scheme = APIKeyHeader(
    name="X-Bot-Secret",
    scheme_name="XBotSecret",
    auto_error=False,
    description="Shared secret for service-to-service calls from bot/web",
)
api_key_scheme = APIKeyHeader(
    name="X-API-Key",
    scheme_name="XApiKey",
    auto_error=False,
    description="Admin API key for admin/destructive operations",
)


async def verify_bot_secret(x_bot_secret: Optional[str] = Security(bot_secret_scheme)) -> None:
    if not x_bot_secret or x_bot_secret != settings.bot_secret_key:
        raise HTTPException(status_code=401, detail="Invalid bot secret")


async def verify_api_key(x_api_key: Optional[str] = Security(api_key_scheme)) -> None:
    if not x_api_key or x_api_key != settings.admin_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")


async def verify_admin_or_bot(
    x_api_key: Optional[str] = Security(api_key_scheme),
    x_bot_secret: Optional[str] = Security(bot_secret_scheme),
) -> None:
    """Allow access with either admin API key or bot service secret."""
    if x_api_key and x_api_key == settings.admin_api_key:
        return
    if x_bot_secret and x_bot_secret == settings.bot_secret_key:
        return
    raise HTTPException(status_code=401, detail="Invalid credentials")


@dataclass
class AdminPrincipal:
    admin_tg_id: Optional[int]


async def verify_admin_actor(
    x_api_key: Optional[str] = Security(api_key_scheme),
    x_admin_tg_id: Optional[str] = Header(None, alias="X-Admin-Tg-Id"),
    x_bot_secret: Optional[str] = Header(None, alias="X-Bot-Secret"),
) -> AdminPrincipal:
    """Деструктивные admin-операции: только X-API-Key. Bot-secret не принимается."""
    if not x_api_key or x_api_key != settings.admin_api_key:
        raise HTTPException(status_code=401, detail="Invalid admin API key")

    admin_tg_id: Optional[int] = None
    if x_admin_tg_id:
        try:
            admin_tg_id = int(x_admin_tg_id)
        except (ValueError, TypeError):
            admin_tg_id = None
    return AdminPrincipal(admin_tg_id=admin_tg_id)


# --- Внешние API-клиенты (Этап 4) ---------------------------------------
# Отдельная auth-схема от bot/web/mobile-mvp выше: не статичный
# env-секрет, а per-client ключ из таблицы api_clients (см.
# services/api_clients/service.py), с scopes и возможностью revoke/rotate
# без деплоя. HTTPBearer — стандартная OpenAPI security scheme для
# `Authorization: Bearer <key>`.
api_client_bearer_scheme = HTTPBearer(
    scheme_name="ApiClientBearer",
    auto_error=False,
    description="API-ключ внешнего клиента публичного API: `Authorization: Bearer <key>`",
)


def verify_api_client(required_scopes: Optional[List[str]] = None):
    """Возвращает FastAPI-зависимость, проверяющую Bearer-ключ клиента и
    (если указаны) наличие всех `required_scopes` среди scopes клиента."""

    async def _dependency(
        credentials: Optional[HTTPAuthorizationCredentials] = Security(api_client_bearer_scheme),
        pool=Depends(get_pool),
    ) -> ApiClient:
        if not credentials or not credentials.credentials:
            raise HTTPException(status_code=401, detail="Missing API key")

        client = await ApiClientService(pool).verify(credentials.credentials)
        if not client:
            raise HTTPException(status_code=401, detail="Invalid or revoked API key")

        missing = set(required_scopes or []) - set(client.scopes)
        if missing:
            raise HTTPException(
                status_code=403,
                detail=f"Missing required scope(s): {', '.join(sorted(missing))}",
            )
        return client

    return _dependency
