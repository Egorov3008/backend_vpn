from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import pytest

from services.web_auth.service import (
    ConfirmStatus,
    PollStatus,
    WebLoginService,
    _hash,
    is_valid_token,
)
from services.web_auth.store import WebLoginTokenRow


@dataclass
class _Rec:
    poll_hash: str
    row: WebLoginTokenRow


class FakeStore:
    """In-memory WebLoginTokenStore с той же семантикой условных UPDATE."""

    def __init__(self):
        self.by_token: Dict[str, _Rec] = {}

    def _now(self):
        return datetime.now(timezone.utc)

    async def create(self, *, token_hash, poll_hash, landing_uid, ttl_seconds):
        self.by_token[token_hash] = _Rec(
            poll_hash,
            WebLoginTokenRow("pending", None, landing_uid, self._now() + timedelta(seconds=ttl_seconds)),
        )

    async def confirm(self, *, token_hash, tg_id) -> Optional[WebLoginTokenRow]:
        rec = self.by_token.get(token_hash)
        if not rec or rec.row.status != "pending" or rec.row.expires_at <= self._now():
            return None
        rec.row = replace(rec.row, status="confirmed", tg_id=tg_id)
        return rec.row

    async def get_by_token(self, token_hash):
        rec = self.by_token.get(token_hash)
        return rec.row if rec else None

    def _by_poll(self, poll_hash):
        return next((r for r in self.by_token.values() if r.poll_hash == poll_hash), None)

    async def consume(self, *, poll_hash):
        rec = self._by_poll(poll_hash)
        if not rec or rec.row.status != "confirmed" or rec.row.expires_at <= self._now():
            return None
        rec.row = replace(rec.row, status="used")
        return rec.row.tg_id

    async def get_by_poll(self, poll_hash):
        rec = self._by_poll(poll_hash)
        return rec.row if rec else None

    def expire_all(self):
        for rec in self.by_token.values():
            rec.row = replace(rec.row, expires_at=self._now() - timedelta(seconds=1))


@pytest.fixture
def store():
    return FakeStore()


@pytest.fixture
def service(store):
    return WebLoginService(store, ttl_seconds=600)


async def test_start_stores_only_hashes(service, store):
    started = await service.start(landing_uid="abc123")
    assert is_valid_token(started.token)
    assert started.expires_in == 600
    assert started.token not in store.by_token
    rec = store.by_token[_hash(started.token)]
    assert rec.poll_hash == _hash(started.poll_secret)
    assert rec.row.landing_uid == "abc123"


async def test_full_flow(service):
    started = await service.start()
    assert (await service.complete(started.poll_secret)).status == PollStatus.PENDING

    confirmed = await service.confirm(started.token, 777)
    assert confirmed.status == ConfirmStatus.CONFIRMED

    done = await service.complete(started.poll_secret)
    assert done.status == PollStatus.CONFIRMED and done.tg_id == 777

    # Сессия выдаётся один раз.
    assert (await service.complete(started.poll_secret)).status == PollStatus.EXPIRED


async def test_token_from_link_cannot_be_used_as_poll_secret(service):
    started = await service.start()
    await service.confirm(started.token, 777)
    assert (await service.complete(started.token)).status == PollStatus.EXPIRED


async def test_repeated_confirm_by_same_user_is_idempotent(service):
    started = await service.start(landing_uid="u1")
    await service.confirm(started.token, 777)
    again = await service.confirm(started.token, 777)
    assert again.status == ConfirmStatus.ALREADY_CONFIRMED
    assert again.landing_uid == "u1"


async def test_confirm_by_other_user_rejected(service):
    started = await service.start()
    await service.confirm(started.token, 777)
    assert (await service.confirm(started.token, 888)).status == ConfirmStatus.EXPIRED
    done = await service.complete(started.poll_secret)
    assert done.tg_id == 777


async def test_expired_token(service, store):
    started = await service.start()
    store.expire_all()
    assert (await service.confirm(started.token, 777)).status == ConfirmStatus.EXPIRED
    assert (await service.complete(started.poll_secret)).status == PollStatus.EXPIRED


@pytest.mark.parametrize("token", ["", "short", "x" * 33, "a" * 31 + "!"])
async def test_malformed_token_rejected_without_store(service, store, token):
    assert (await service.confirm(token, 777)).status == ConfirmStatus.EXPIRED


@pytest.mark.parametrize("tg_id", [0, -5])
async def test_non_telegram_tg_id_rejected(service, tg_id):
    started = await service.start()
    assert (await service.confirm(started.token, tg_id)).status == ConfirmStatus.EXPIRED


async def test_complete_without_cookie(service):
    assert (await service.complete(None)).status == PollStatus.EXPIRED
