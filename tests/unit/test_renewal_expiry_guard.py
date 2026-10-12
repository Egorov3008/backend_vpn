"""Продление по платежу: целевой срок фиксируется до панели и повтор не добавляет период второй раз."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from services.core.keys.utils.renewal import KeyRenewal
from services.system.maintenance import maintenance_mode


@pytest.fixture(autouse=True)
def _maintenance_mode_off(monkeypatch):
    monkeypatch.setattr(maintenance_mode, "is_enabled", AsyncMock(return_value=False))


def _renewal(computed_expiry):
    """computed_expiry — срок, который refresh_key вычислит в этой попытке (зависит от «сейчас»)."""
    xui = MagicMock()
    xui.extend_client_key = AsyncMock(return_value=True)
    md = MagicMock()
    md.keys.update = AsyncMock()
    refresh = MagicMock()
    refresh.refresh_key = MagicMock(
        side_effect=lambda key, *a, **kw: setattr(key, "expiry_time", computed_expiry) or key
    )
    resetter = MagicMock()
    resetter.reset_key_after_renewal = AsyncMock()
    kr = KeyRenewal(model_data=md, xui_session=xui, refresh_key=refresh, resetter=resetter)
    return kr, xui


def _key():
    k = MagicMock()
    k.expiry_time = 2000
    k.email = "a@b.c"
    return k


@pytest.mark.asyncio
async def test_guard_value_is_applied_to_panel():
    kr, xui = _renewal(computed_expiry=5000)
    guard = AsyncMock(return_value=4321)

    out = await kr.extension_key(
        _key(), conn=MagicMock(), server=MagicMock(), tariff=MagicMock(), number_of_months=1,
        expiry_guard=guard,
    )

    guard.assert_awaited_once_with(5000)
    assert out.expiry_time == 4321
    sent = xui.extend_client_key.await_args.args[0]
    assert sent.expiry_time == 4321


@pytest.mark.asyncio
async def test_retry_applies_the_same_absolute_expiry_as_first_attempt():
    """Первая попытка зафиксировала срок 5000. Повтор позже (now изменился, вычисленный срок 9000)
    должен отправить в панель 5000, а не добавить период к уже продлённому ключу."""
    stored = {}

    async def guard(target):
        return stored.setdefault("expiry", target)

    kr1, xui1 = _renewal(computed_expiry=5000)
    await kr1.extension_key(
        _key(), conn=MagicMock(), server=MagicMock(), tariff=MagicMock(), number_of_months=1,
        expiry_guard=guard,
    )

    kr2, xui2 = _renewal(computed_expiry=9000)  # повтор позже: новый расчёт дал бы другой срок
    await kr2.extension_key(
        _key(), conn=MagicMock(), server=MagicMock(), tariff=MagicMock(), number_of_months=1,
        expiry_guard=guard,
    )

    assert xui1.extend_client_key.await_args.args[0].expiry_time == 5000
    assert xui2.extend_client_key.await_args.args[0].expiry_time == 5000


@pytest.mark.asyncio
async def test_without_guard_behaviour_is_unchanged():
    """Прямое продление (PATCH /keys/{email}) без платежа: срок из расчёта, без фиксации."""
    kr, xui = _renewal(computed_expiry=7000)

    out = await kr.extension_key(
        _key(), conn=MagicMock(), server=MagicMock(), tariff=MagicMock(), number_of_months=1,
    )

    assert out.expiry_time == 7000
    assert xui.extend_client_key.await_args.args[0].expiry_time == 7000
