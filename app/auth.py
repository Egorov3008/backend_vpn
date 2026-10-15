import hmac
from dataclasses import dataclass
from typing import FrozenSet, List, Optional, Protocol
from urllib.parse import urlparse

from fastapi import Depends, Header, HTTPException, Request, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

import config
from app.core.miniapp_session import MiniAppSessionError, admin_sessions, verify_session_token, web_sessions
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


landing_internal_scheme = APIKeyHeader(
    name="X-Landing-Internal-Secret",
    scheme_name="XLandingInternalSecret",
    auto_error=False,
    description="Bot-only secret for landing claim/mark-converted (never injected by nginx)",
)


async def verify_landing_internal_secret(
    x_landing_internal_secret: Optional[str] = Security(landing_internal_scheme),
) -> None:
    """Вторичный секрет бота для /landing/claim и /landing/mark-converted.

    Дополняет X-Bot-Secret: nginx подставляет тот же X-Bot-Secret на публичных
    путях /api/v1/landing/*, а этот секрет nginx не отдаёт. Пустой секрет в
    настройках = маршруты закрыты для всех.
    """
    expected = settings.landing_internal_secret
    if not expected or not x_landing_internal_secret or not hmac.compare_digest(
        x_landing_internal_secret, expected
    ):
        raise HTTPException(status_code=401, detail="Invalid landing internal secret")


async def verify_api_key(x_api_key: Optional[str] = Security(api_key_scheme)) -> None:
    if not x_api_key or x_api_key != settings.admin_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")


@dataclass
class AdminPrincipal:
    admin_tg_id: Optional[int]


# --- Админ-аутентификация ----------------------------------------------------
# Два способа доказать, что запрос от админа, за одним протоколом:
#   * ApiKeyAdminAuth — бот: X-API-Key + X-Admin-Tg-Id (заголовку tg_id сервер
#     верит, потому что ключ есть только у бота);
#   * SessionAdminAuth — веб-админка: Bearer-токен из POST /admin/session,
#     tg_id берётся из подписанного токена и на каждом запросе сверяется с ADMIN_ID.
# Стратегия возвращает None, если её учётных данных в запросе нет, и бросает
# 401/403, если они есть, но неверны. Новый способ входа = новый класс в
# ADMIN_AUTHENTICATORS, проверки эндпоинтов не меняются.
admin_session_scheme = HTTPBearer(
    scheme_name="AdminSession",
    auto_error=False,
    description="Сессия веб-админки из POST /api/v1/admin/session: `Authorization: Bearer <token>`",
)


@dataclass(frozen=True)
class AdminCredentials:
    api_key: Optional[str] = None
    admin_tg_id_header: Optional[str] = None
    session_token: Optional[str] = None


class AdminAuthenticator(Protocol):
    def authenticate(self, creds: AdminCredentials) -> Optional[AdminPrincipal]: ...


def admin_ids() -> FrozenSet[int]:
    """Telegram ID админов из ADMIN_ID (читается при каждом вызове)."""
    ids = set()
    for raw in config.ADMIN_ID:
        try:
            ids.add(int(raw))
        except (TypeError, ValueError):
            continue
    ids.discard(0)  # значение по умолчанию "[0]" — не админ
    return frozenset(ids)


class ApiKeyAdminAuth:
    def authenticate(self, creds: AdminCredentials) -> Optional[AdminPrincipal]:
        if not creds.api_key:
            return None
        if not hmac.compare_digest(creds.api_key, settings.admin_api_key):
            raise HTTPException(status_code=401, detail="Invalid admin API key")
        admin_tg_id: Optional[int] = None
        if creds.admin_tg_id_header:
            try:
                admin_tg_id = int(creds.admin_tg_id_header)
            except (ValueError, TypeError):
                admin_tg_id = None
        return AdminPrincipal(admin_tg_id=admin_tg_id)


class SessionAdminAuth:
    def authenticate(self, creds: AdminCredentials) -> Optional[AdminPrincipal]:
        if not creds.session_token:
            return None
        try:
            tg_id = admin_sessions.verify(creds.session_token)
        except MiniAppSessionError as e:
            raise HTTPException(status_code=401, detail=str(e))
        # Удалили из ADMIN_ID — уже выданная сессия перестаёт работать сразу.
        if tg_id not in admin_ids():
            raise HTTPException(status_code=403, detail="Not an admin")
        return AdminPrincipal(admin_tg_id=tg_id)


ADMIN_AUTHENTICATORS: tuple[AdminAuthenticator, ...] = (ApiKeyAdminAuth(), SessionAdminAuth())


def authenticate_admin(creds: AdminCredentials) -> Optional[AdminPrincipal]:
    for authenticator in ADMIN_AUTHENTICATORS:
        principal = authenticator.authenticate(creds)
        if principal is not None:
            return principal
    return None


def _bearer_token(credentials: Optional[HTTPAuthorizationCredentials]) -> Optional[str]:
    # isinstance: при прямом вызове из тестов сюда может прийти дефолт Security(...)
    if isinstance(credentials, HTTPAuthorizationCredentials) and credentials.credentials:
        return credentials.credentials
    return None


async def verify_admin_or_bot(
    x_api_key: Optional[str] = Security(api_key_scheme),
    x_bot_secret: Optional[str] = Security(bot_secret_scheme),
    admin_session: Optional[HTTPAuthorizationCredentials] = Security(admin_session_scheme),
) -> None:
    """Чтение в админке: ключ админа, сервисный секрет бота или админ-сессия."""
    if x_bot_secret and x_bot_secret == settings.bot_secret_key:
        return
    principal = authenticate_admin(
        AdminCredentials(api_key=x_api_key, session_token=_bearer_token(admin_session))
    )
    if principal is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")


async def verify_admin_actor(
    x_api_key: Optional[str] = Security(api_key_scheme),
    x_admin_tg_id: Optional[str] = Header(None, alias="X-Admin-Tg-Id"),
    x_bot_secret: Optional[str] = Header(None, alias="X-Bot-Secret"),
    admin_session: Optional[HTTPAuthorizationCredentials] = Security(admin_session_scheme),
) -> AdminPrincipal:
    """Деструктивные admin-операции: X-API-Key или админ-сессия. Bot-secret не принимается."""
    principal = authenticate_admin(
        AdminCredentials(
            api_key=x_api_key,
            admin_tg_id_header=x_admin_tg_id,
            session_token=_bearer_token(admin_session),
        )
    )
    if principal is None:
        raise HTTPException(status_code=401, detail="Invalid admin API key")
    return principal


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


def _request_domain(request: Request) -> Optional[str]:
    """Хост запроса из Origin, а если его нет (не-браузерный клиент,
    server-to-server) — из Referer. Порт/схема отбрасываются: домен
    привязки сравнивается по хосту."""
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        return None
    host = urlparse(origin).hostname
    return host.lower() if host else None


def verify_api_client(required_scopes: Optional[List[str]] = None):
    """Возвращает FastAPI-зависимость, проверяющую Bearer-ключ клиента,
    (если указаны) наличие всех `required_scopes` среди scopes клиента, и
    (если у клиента задан `allowed_domain`) что запрос пришёл с этого домена."""

    async def _dependency(
        request: Request,
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

        if client.allowed_domain:
            request_domain = _request_domain(request)
            if request_domain != client.allowed_domain.lower():
                raise HTTPException(
                    status_code=403,
                    detail="This API key is not allowed to be used from this domain",
                )

        return client

    return _dependency


# --- Telegram Mini App ----------------------------------------------------
# Пользовательская сессия (не сервисный секрет): токен выдаёт
# POST /api/v1/miniapp/auth после проверки initData, в нём tg_id. Эндпоинты
# /api/v1/miniapp/* берут пользователя только отсюда — tg_id из запроса
# не принимается вовсе.
miniapp_bearer_scheme = HTTPBearer(
    scheme_name="MiniAppSession",
    auto_error=False,
    description="Сессия Telegram Mini App из POST /api/v1/miniapp/auth: `Authorization: Bearer <token>`",
)


@dataclass
class UserPrincipal:
    """Пользователь кабинета (Mini App или веб): ``tg_id`` из подписанной сессии."""

    tg_id: int


# Старое имя — используется в api/v1/miniapp.py и тестах.
MiniAppPrincipal = UserPrincipal


async def verify_miniapp_session(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(miniapp_bearer_scheme),
) -> UserPrincipal:
    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Missing session token")
    try:
        tg_id = verify_session_token(credentials.credentials)
    except MiniAppSessionError as e:
        raise HTTPException(status_code=401, detail=str(e))
    return UserPrincipal(tg_id=tg_id)


# --- Веб-кабинет -----------------------------------------------------------
# Сессия в HttpOnly cookie (JS её не читает), SameSite=Strict, Path=/api/v1/web.
# Изменяющие запросы дополнительно требуют заголовок X-Requested-With: web:
# браузер не отправит его с чужого сайта без CORS-preflight, а CORS для
# /api/v1/web/ не включён — это вторая линия защиты от CSRF после SameSite.
WEB_SESSION_COOKIE = "web_session"
WEB_CSRF_HEADER = "X-Requested-With"
WEB_CSRF_HEADER_VALUE = "web"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def require_web_csrf_header(request: Request) -> None:
    if request.method in _SAFE_METHODS:
        return
    if request.headers.get(WEB_CSRF_HEADER) != WEB_CSRF_HEADER_VALUE:
        raise HTTPException(status_code=403, detail="Missing X-Requested-With header")


async def verify_web_session(request: Request) -> UserPrincipal:
    require_web_csrf_header(request)
    token = request.cookies.get(WEB_SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Not logged in")
    try:
        tg_id = web_sessions.verify(token)
    except MiniAppSessionError as e:
        raise HTTPException(status_code=401, detail=str(e))
    return UserPrincipal(tg_id=tg_id)
