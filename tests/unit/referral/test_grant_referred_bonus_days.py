"""
Тесты для _grant_referred_bonus_days (#6/#7).

Раньше метод делал только raw UPDATE keys.expiry_time — без 3x-UI panel и без
cache.keys.set, поэтому VPN реферала отключался в оригинальное время, а бот
показывал старый expiry до 3-часового sync. Теперь продление согласованно
обновляет panel + DB + cache, а ошибки DB/cache пробрасываются (не глушатся).
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from models import Key
from services.cache.key_manager import CacheKeyManager
from services.core.referral.bonus_service import ReferralBonusService


def _make_key(email="user@example.com", expiry_time=1_000_000):
    return Key(
        tg_id=200,
        client_id="client-1",
        email=email,
        expiry_time=expiry_time,
        key="vless://...",
        inbound_id=1,
    )


def _build_svc(*, xui=None, cache=None, keys_get=None):
    service = MagicMock()
    service.users = MagicMock()
    service.data_service = MagicMock()
    service.keys = MagicMock()
    service.keys.get_data = AsyncMock(side_effect=keys_get) if keys_get else AsyncMock()
    service.keys.update = AsyncMock()
    xui_mock = xui if xui is not None else MagicMock()
    xui_mock.extend_client_key = AsyncMock(return_value=True)
    cache_mock = cache if cache is not None else MagicMock()
    cache_mock.keys = MagicMock()
    cache_mock.keys.set = AsyncMock()
    return ReferralBonusService(service, xui_session=xui_mock, cache=cache_mock), xui_mock, cache_mock, service


def _conn_with_emails(emails):
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[{"email": e} for e in emails])
    return conn


class TestGrantBonusDays:
    async def test_extends_panel_db_and_cache(self):
        key = _make_key(expiry_time=1_000_000)
        svc, xui, cache, service = _build_svc(keys_get=lambda email, conn=None: key)
        conn = _conn_with_emails([key.email])

        await svc._grant_referred_bonus_days(conn, 200)

        expected_expiry = 1_000_000 + svc.BONUS_DAYS_MS
        # expiry_time bumped on the same Key object passed to panel
        xui.extend_client_key.assert_awaited_once()
        extended_key = xui.extend_client_key.await_args.args[0]
        assert extended_key.expiry_time == expected_expiry
        # DB
        service.keys.update.assert_awaited_once()
        args, kwargs = service.keys.update.await_args
        assert args[0] is conn
        assert args[1].expiry_time == expected_expiry
        assert kwargs["search_data"] == {"email": key.email}
        # cache
        cache.keys.set.assert_awaited_once_with(
            CacheKeyManager.key(key.email), args[1]
        )

    async def test_no_keys_is_noop(self):
        svc, xui, cache, service = _build_svc()
        conn = _conn_with_emails([])

        await svc._grant_referred_bonus_days(conn, 200)

        xui.extend_client_key.assert_not_awaited()
        service.keys.update.assert_not_awaited()
        cache.keys.set.assert_not_awaited()

    async def test_panel_failure_still_updates_db_and_cache(self):
        """Panel best-effort: при сбое панели DB+cache всё равно обновляются."""
        key = _make_key(expiry_time=2_000_000)
        svc, xui, cache, service = _build_svc(keys_get=lambda email, conn=None: key)
        xui.extend_client_key = AsyncMock(return_value=False)

        conn = _conn_with_emails([key.email])

        await svc._grant_referred_bonus_days(conn, 200)

        xui.extend_client_key.assert_awaited_once()
        service.keys.update.assert_awaited_once()
        cache.keys.set.assert_awaited_once()

    async def test_panel_exception_still_updates_db_and_cache(self):
        key = _make_key()
        svc, xui, cache, service = _build_svc(keys_get=lambda email, conn=None: key)
        xui.extend_client_key = AsyncMock(side_effect=RuntimeError("panel down"))

        conn = _conn_with_emails([key.email])

        # не должно бросать — panel-ошибка глушится намеренно (best-effort),
        # DB+cache обновляются, sync подлечит panel.
        await svc._grant_referred_bonus_days(conn, 200)

        service.keys.update.assert_awaited_once()
        cache.keys.set.assert_awaited_once()

    async def test_missing_key_skipped(self):
        svc, xui, cache, service = _build_svc(keys_get=lambda email, conn=None: None)
        conn = _conn_with_emails(["ghost@example.com"])

        await svc._grant_referred_bonus_days(conn, 200)

        xui.extend_client_key.assert_not_awaited()
        service.keys.update.assert_not_awaited()
        cache.keys.set.assert_not_awaited()

    async def test_db_error_propagates_not_swallowed(self):
        """#7: ошибка DB больше не глушится — пробрасывается наружу."""
        key = _make_key()
        svc, xui, cache, service = _build_svc(keys_get=lambda email, conn=None: key)
        service.keys.update = AsyncMock(side_effect=RuntimeError("DB down"))
        conn = _conn_with_emails([key.email])

        with pytest.raises(RuntimeError, match="DB down"):
            await svc._grant_referred_bonus_days(conn, 200)

    async def test_raises_when_xui_or_cache_not_configured(self):
        """С ключами, но без xui/cache — явная ошибка (не silent raw SQL)."""
        service = MagicMock()
        service.keys = MagicMock()
        service.keys.get_data = AsyncMock()
        svc = ReferralBonusService(service)  # без xui/cache
        conn = _conn_with_emails(["user@example.com"])

        with pytest.raises(RuntimeError, match="xui_session и cache"):
            await svc._grant_referred_bonus_days(conn, 200)