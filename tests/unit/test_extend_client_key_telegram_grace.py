"""Продление ключа из окна «только Telegram» возвращает клиенту инбаунды."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from client import XUISession
from models import Key


def _session(panel_inbound_ids, set_inbounds_ok=True):
    session = XUISession.__new__(XUISession)
    session.ensure_auth = AsyncMock()
    session._ensure_standalone = AsyncMock()
    standalone = MagicMock()
    standalone.get = AsyncMock(return_value={
        "obj": {
            "client": {"id": 1, "email": "u@example.com", "tgId": 123, "expiryTime": 1, "enable": True},
            "inboundIds": panel_inbound_ids,
        }
    })
    standalone.update = AsyncMock(return_value={"success": True})
    session._standalone = standalone
    session.set_inbounds = AsyncMock(return_value=set_inbounds_ok)
    session.set_external_subscription = AsyncMock(return_value=True)
    return session


def _key():
    return Key(
        tg_id=123, client_id="uuid", email="u@example.com", expiry_time=1_900_000_000_000,
        key="https://sub.example.com/u@example.com", inbound_id=1, limit_ip=3,
        telegram_grace_until=1_800_000_000_000,
    )


@pytest.mark.asyncio
async def test_renewal_from_grace_reattaches_paid_inbounds(monkeypatch):
    from services.core.keys.utils import inbounds

    monkeypatch.setattr(inbounds, "PAID_OVERLAY_INBOUNDS", [2, 3])
    session = _session(panel_inbound_ids=[])
    key = _key()

    assert await session.extend_client_key(key) is True

    session.set_inbounds.assert_awaited_once_with("u@example.com", [2, 3])
    assert session._standalone.update.await_args.args[1]["expiryTime"] == 1_900_000_000_000
    assert key.telegram_grace_until is None


@pytest.mark.asyncio
async def test_renewal_of_active_key_keeps_inbounds():
    session = _session(panel_inbound_ids=[2, 3])
    key = _key()

    assert await session.extend_client_key(key) is True

    session.set_inbounds.assert_not_called()


@pytest.mark.asyncio
async def test_renewal_fails_when_inbounds_not_restored():
    session = _session(panel_inbound_ids=[], set_inbounds_ok=False)
    key = _key()

    # Иначе платёж прошёл бы успешно, а у клиента остался бы только Telegram.
    assert await session.extend_client_key(key) is False
    assert key.telegram_grace_until == 1_800_000_000_000
