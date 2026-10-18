"""Окно «только Telegram» после окончания подписки (telegram_grace.py).

3x-ui >= v3.8.0 не отдаёт внешние ссылки просроченного клиента, поэтому бэкенд
отвязывает клиента от инбаундов и сдвигает его expiryTime в панели на конец окна,
а keys.expiry_time оставляет сроком оплаты.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from models import Key
from services.core.keys.utils import telegram_grace as tg
from services.core.keys.utils.telegram_grace import TelegramGrace, needs_grace
from services.system.maintenance import maintenance_mode

DAY = 24 * 3600 * 1000
NOW = 1_800_000_000_000
EXTERNAL = "https://sub.example.com/external-test"


@pytest.fixture(autouse=True)
def _maintenance_off(monkeypatch):
    monkeypatch.setattr(maintenance_mode, "is_enabled", AsyncMock(return_value=False))


def _paid():
    return SimpleNamespace(id=5, amount=199)


def _free():
    return SimpleNamespace(id=99, amount=0)


def _key(expiry=NOW - 3600_000, **kw):
    return Key(
        tg_id=123, client_id="uuid", email="u@example.com", expiry_time=expiry,
        key="https://sub.example.com/u@example.com", inbound_id=1, tariff_id=5, **kw,
    )


# --- needs_grace -----------------------------------------------------------

def test_needs_grace_for_recently_expired_paid_key():
    assert needs_grace(_key(), _paid(), NOW, 7) is True


@pytest.mark.parametrize(
    "key, tariff, days",
    [
        (_key(expiry=NOW + 3600_000), _paid(), 7),           # ещё действует
        (_key(expiry=NOW - 8 * DAY), _paid(), 7),            # окно уже прошло
        (_key(), _free(), 7),                                # не подписка
        (_key(), None, 7),                                   # тариф не найден
        (_key(), _paid(), 0),                                # выключено
        (_key(expiry=0), _paid(), 7),                        # бессрочный
        (_key(telegram_grace_until=NOW + DAY), _paid(), 7),  # окно уже открыто
        (_key(landing_uid="abc"), _paid(), 7),               # анонимный лендинг
    ],
)
def test_needs_grace_false(key, tariff, days):
    assert needs_grace(key, tariff, NOW, days) is False


def test_converted_landing_key_gets_grace():
    assert needs_grace(_key(landing_uid="abc", converted_tg_id=123), _paid(), NOW, 7) is True


# --- TelegramGrace.run -----------------------------------------------------

def _service(keys, *, set_inbounds_ok=True, update_exc=None, days=7, external=EXTERNAL, fresh=None):
    model_data = MagicMock()
    model_data.keys.get_all = AsyncMock(return_value=keys)
    # get_data под локом возвращает «свежий» ключ (по умолчанию тот же объект).
    model_data.keys.get_data = AsyncMock(side_effect=lambda email, pool: fresh or keys[0])
    model_data.keys.update = AsyncMock()
    model_data.tariffs.get_data = AsyncMock(return_value=_paid())
    xui = MagicMock()
    xui.set_inbounds = AsyncMock(return_value=set_inbounds_ok)
    xui.update_standalone_client = AsyncMock(side_effect=update_exc)
    xui.set_external_subscription = AsyncMock(return_value=True)
    svc = TelegramGrace(
        model_data, xui, pool=MagicMock(), grace_days=days,
        external_subscription_url=external, now_ms=lambda: NOW,
    )
    return svc, model_data, xui


@pytest.mark.asyncio
async def test_run_opens_window():
    key = _key()
    paid_expiry = key.expiry_time
    svc, model_data, xui = _service([key])

    result = await svc.run()

    assert result.opened == 1 and result.failed == 0
    until = paid_expiry + 7 * DAY
    xui.set_inbounds.assert_awaited_once_with("u@example.com", [])
    xui.update_standalone_client.assert_awaited_once_with(
        "u@example.com", expiryTime=until, enable=True
    )
    xui.set_external_subscription.assert_awaited_once_with("u@example.com", EXTERNAL)
    # В БД срок оплаты не меняется, конец окна — отдельным полем.
    assert key.expiry_time == paid_expiry
    assert key.telegram_grace_until == until
    model_data.keys.update.assert_awaited_once()


@pytest.mark.asyncio
async def test_detach_failure_does_not_extend_panel_expiry():
    key = _key()
    svc, model_data, xui = _service([key], set_inbounds_ok=False)

    result = await svc.run()

    assert result.failed == 1
    # Без отвязки инбаундов сдвиг срока открыл бы полный VPN до конца окна.
    xui.update_standalone_client.assert_not_called()
    assert key.telegram_grace_until is None
    model_data.keys.update.assert_not_called()


@pytest.mark.asyncio
async def test_panel_update_failure_leaves_key_for_retry():
    key = _key()
    svc, model_data, _ = _service([key], update_exc=RuntimeError("panel down"))

    result = await svc.run()

    assert result.failed == 1
    assert key.telegram_grace_until is None
    model_data.keys.update.assert_not_called()


@pytest.mark.asyncio
async def test_key_renewed_before_lock_is_skipped():
    stale = _key()
    renewed = _key(expiry=NOW + 30 * DAY)
    svc, _, xui = _service([stale], fresh=renewed)

    result = await svc.run()

    assert result.opened == 0 and result.skipped == 1
    xui.set_inbounds.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("days, external", [(0, EXTERNAL), (7, "")])
async def test_disabled_does_nothing(days, external):
    svc, model_data, xui = _service([_key()], days=days, external=external)

    result = await svc.run()

    assert result.opened == 0
    model_data.keys.get_all.assert_not_called()
    xui.set_inbounds.assert_not_called()


@pytest.mark.asyncio
async def test_maintenance_skips_run(monkeypatch):
    monkeypatch.setattr(maintenance_mode, "is_enabled", AsyncMock(return_value=True))
    svc, model_data, _ = _service([_key()])

    await svc.run()

    model_data.keys.get_all.assert_not_called()


def test_grace_until_uses_paid_expiry():
    assert tg.grace_until(_key(expiry=NOW), 7) == NOW + 7 * DAY
