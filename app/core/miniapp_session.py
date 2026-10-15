"""Сессии Telegram Mini App и веб-кабинета.

Mini App меняет проверенный ``initData`` (``POST /api/v1/miniapp/auth``) на
токен и шлёт его как ``Authorization: Bearer <token>``. Веб-кабинет получает
токен после подтверждения входа в боте (``api/v1/web.py``) и хранит его в
HttpOnly cookie. В обоих случаях ``tg_id`` берётся только из токена.

Оба вида подписываются ``MINIAPP_SESSION_SECRET``, но с разными метками
(``app/core/session_tokens.py``), поэтому токен Mini App не принимается
веб-кабинетом и наоборот.
"""

from __future__ import annotations

from typing import Optional

from app.core.session_tokens import SessionTokenError, SessionTokenService
from config import settings

# Старое имя исключения — на него ссылаются эндпоинты и тесты.
MiniAppSessionError = SessionTokenError

# Метка "miniapp-session-v1" не меняется: иначе уже выданные токены перестанут
# проходить проверку сразу после выката.
miniapp_sessions = SessionTokenService(
    label=b"miniapp-session-v1",
    secret=lambda: settings.miniapp_session_secret,
    default_ttl=lambda: settings.miniapp_session_ttl_seconds,
)

web_sessions = SessionTokenService(
    label=b"web-session-v1",
    secret=lambda: settings.miniapp_session_secret,
    default_ttl=lambda: settings.web_session_ttl_seconds,
)


def issue_session_token(tg_id: int, ttl_seconds: Optional[int] = None) -> tuple[str, int]:
    """Токен Mini App: ``(token, expires_in_seconds)``."""
    return miniapp_sessions.issue(tg_id, ttl_seconds)


def verify_session_token(token: str) -> int:
    """``tg_id`` из токена Mini App или :class:`MiniAppSessionError`."""
    return miniapp_sessions.verify(token)
