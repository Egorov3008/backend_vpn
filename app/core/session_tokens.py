"""Подписанные токены пользовательских сессий (Mini App, веб-кабинет).

Формат: ``base64url(json payload) + "." + base64url(HMAC-SHA256)``, в payload
``tg_id`` и ``exp``. Только stdlib — тот же подход, что у подписанных cookie
лендинга (``api/v1/landing.py``).

Ключ подписи выводится из секрета с меткой (``label``), поэтому токен одного
клиента не принимается другим: токен Mini App не откроет веб-кабинет и
наоборот, даже если секрет общий.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from typing import Callable, Optional


class SessionTokenError(ValueError):
    """Токен повреждён, подделан или истёк."""


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class SessionTokenService:
    """Выпуск и проверка токенов сессии одного клиента.

    Секрет и TTL передаются функциями, а не значениями: настройки читаются в
    момент вызова, так тесты могут подменять ``settings`` через monkeypatch.
    """

    def __init__(
        self,
        *,
        label: bytes,
        secret: Callable[[], str],
        default_ttl: Callable[[], int],
    ) -> None:
        self._label = label
        self._secret = secret
        self._default_ttl = default_ttl

    def _signing_key(self) -> bytes:
        return hmac.new(self._secret().encode(), self._label, hashlib.sha256).digest()

    def _sign(self, body: str) -> str:
        return _b64encode(hmac.new(self._signing_key(), body.encode(), hashlib.sha256).digest())

    def issue(self, tg_id: int, ttl_seconds: Optional[int] = None) -> tuple[str, int]:
        """Вернуть ``(token, expires_in_seconds)`` для ``tg_id``."""
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl()
        payload = {"tg_id": tg_id, "exp": int(time.time()) + ttl}
        body = _b64encode(json.dumps(payload, separators=(",", ":")).encode())
        return f"{body}.{self._sign(body)}", ttl

    def verify(self, token: str) -> int:
        """Вернуть ``tg_id`` из валидного токена или бросить :class:`SessionTokenError`."""
        body, sep, sig = token.partition(".")
        if not sep or not body or not sig:
            raise SessionTokenError("Malformed session token")

        if not hmac.compare_digest(self._sign(body), sig):
            raise SessionTokenError("Invalid session token")

        try:
            payload = json.loads(_b64decode(body))
            tg_id = payload["tg_id"]
            exp = payload["exp"]
        except (binascii.Error, ValueError, KeyError, TypeError):
            raise SessionTokenError("Malformed session token")
        # bool — подкласс int; true/false в tg_id не принимаем.
        if type(tg_id) is not int or type(exp) is not int:
            raise SessionTokenError("Malformed session token")
        if exp < time.time():
            raise SessionTokenError("Session expired")
        return tg_id
