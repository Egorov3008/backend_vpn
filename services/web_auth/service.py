"""Логика входа в веб-кабинет через бота.

1. Браузер: ``start()`` → токен для ссылки ``t.me/<бот>?start=weblogin_<token>``
   и секрет опроса (кладётся в HttpOnly cookie этого браузера).
2. Бот: пользователь нажимает «Подтвердить» → ``confirm(token, tg_id)``.
3. Браузер: ``complete(poll_secret)`` → ``tg_id``, один раз.

Токен из ссылки сам по себе сессию не даёт: её забирает только браузер с
секретом опроса. Зависит от хранилища через протокол (`WebLoginTokenStore`),
поэтому тестируется без БД.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from services.web_auth.store import WebLoginTokenStore

# Параметр /start в Telegram: до 64 символов [A-Za-z0-9_-]. "weblogin_" + 32 = 41.
DEEP_LINK_PREFIX = "weblogin_"
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32}$")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def is_valid_token(token: str) -> bool:
    return bool(_TOKEN_RE.match(token or ""))


@dataclass(frozen=True)
class StartedLogin:
    token: str
    poll_secret: str
    expires_in: int


class ConfirmStatus(str, Enum):
    CONFIRMED = "confirmed"
    ALREADY_CONFIRMED = "already_confirmed"  # тот же пользователь повторно нажал кнопку
    EXPIRED = "expired"  # не найден, истёк, уже использован или подтверждён другим


@dataclass(frozen=True)
class ConfirmResult:
    status: ConfirmStatus
    landing_uid: Optional[str] = None


class PollStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    EXPIRED = "expired"


@dataclass(frozen=True)
class PollResult:
    status: PollStatus
    tg_id: Optional[int] = None


class WebLoginService:
    def __init__(self, store: WebLoginTokenStore, *, ttl_seconds: int):
        self._store = store
        self._ttl = ttl_seconds

    async def start(self, *, landing_uid: Optional[str] = None) -> StartedLogin:
        token = secrets.token_urlsafe(24)  # 32 символа
        poll_secret = secrets.token_urlsafe(32)
        await self._store.create(
            token_hash=_hash(token),
            poll_hash=_hash(poll_secret),
            landing_uid=landing_uid,
            ttl_seconds=self._ttl,
        )
        return StartedLogin(token=token, poll_secret=poll_secret, expires_in=self._ttl)

    async def confirm(self, token: str, tg_id: int) -> ConfirmResult:
        if tg_id <= 0 or not is_valid_token(token):
            return ConfirmResult(ConfirmStatus.EXPIRED)
        token_hash = _hash(token)
        row = await self._store.confirm(token_hash=token_hash, tg_id=tg_id)
        if row is not None:
            return ConfirmResult(ConfirmStatus.CONFIRMED, landing_uid=row.landing_uid)
        existing = await self._store.get_by_token(token_hash)
        if existing is not None and existing.status != "pending" and existing.tg_id == tg_id:
            return ConfirmResult(ConfirmStatus.ALREADY_CONFIRMED, landing_uid=existing.landing_uid)
        return ConfirmResult(ConfirmStatus.EXPIRED)

    async def complete(self, poll_secret: Optional[str]) -> PollResult:
        if not poll_secret:
            return PollResult(PollStatus.EXPIRED)
        poll_hash = _hash(poll_secret)
        tg_id = await self._store.consume(poll_hash=poll_hash)
        if tg_id is not None:
            return PollResult(PollStatus.CONFIRMED, tg_id=tg_id)
        row = await self._store.get_by_poll(poll_hash)
        if row is not None and row.status == "pending" and row.expires_at > datetime.now(timezone.utc):
            return PollResult(PollStatus.PENDING)
        return PollResult(PollStatus.EXPIRED)
