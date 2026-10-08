"""Short-lived session tokens for the Telegram Mini App.

The Mini App exchanges its verified ``initData`` (``POST /api/v1/miniapp/auth``)
for one of these tokens and sends it as ``Authorization: Bearer <token>``.
The token carries ``tg_id`` — every ``/api/v1/miniapp/*`` endpoint takes the
user from here, never from the request.

Format: ``base64url(json payload) + "." + base64url(HMAC-SHA256)`` — stdlib
only, same approach as the signed landing cookies (``api/v1/landing.py``).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from typing import Optional

from config import settings

_KEY_LABEL = b"miniapp-session-v1"


class MiniAppSessionError(ValueError):
    """Raised when a session token is malformed, forged or expired."""


def _signing_key() -> bytes:
    # Falls back to BOT_SECRET_KEY like LANDING_COOKIE_SECRET does. The key is
    # derived with a fixed label so a token can't be replayed as a landing
    # cookie signature (or vice versa) when both share the fallback secret.
    secret = settings.miniapp_session_secret or settings.bot_secret_key
    return hmac.new(secret.encode(), _KEY_LABEL, hashlib.sha256).digest()


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_session_token(tg_id: int, ttl_seconds: Optional[int] = None) -> tuple[str, int]:
    """Return ``(token, expires_in_seconds)`` for ``tg_id``."""
    ttl = ttl_seconds if ttl_seconds is not None else settings.miniapp_session_ttl_seconds
    payload = {"tg_id": tg_id, "exp": int(time.time()) + ttl}
    body = _b64encode(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64encode(hmac.new(_signing_key(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}", ttl


def verify_session_token(token: str) -> int:
    """Return the ``tg_id`` from a valid token or raise :class:`MiniAppSessionError`."""
    body, sep, sig = token.partition(".")
    if not sep or not body or not sig:
        raise MiniAppSessionError("Malformed session token")

    expected = _b64encode(hmac.new(_signing_key(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, sig):
        raise MiniAppSessionError("Invalid session token")

    try:
        payload = json.loads(_b64decode(body))
        tg_id = payload["tg_id"]
        exp = payload["exp"]
    except (binascii.Error, ValueError, KeyError, TypeError):
        raise MiniAppSessionError("Malformed session token")
    if not isinstance(tg_id, int) or not isinstance(exp, int):
        raise MiniAppSessionError("Malformed session token")
    if exp < time.time():
        raise MiniAppSessionError("Session expired")
    return tg_id
