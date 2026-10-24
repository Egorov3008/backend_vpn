"""Акция возврата: кому пишем, что выпускаем, как считаем результат.

Воронку запускаем напрямую (как планировщик за один цикл) на живой БД; сообщения
ловит заглушка Telegram.
"""
import json
import time

import pytest

from tests.integration.conftest import STANDARD_TARIFF_ID
from tests.integration.helpers import insert_key, register_user

DAY_MS = 24 * 3600 * 1000
MINIAPP_URL = "https://app.example.test/miniapp/"
WEB_HEADERS = {"X-Requested-With": "web"}


@pytest.fixture
def winback(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "winback_enabled", True)
    monkeypatch.setattr(settings, "winback_control_percent", 0)
    monkeypatch.setattr(settings, "miniapp_url", MINIAPP_URL)
    return settings


async def _paid_user_with_lapsed_key(backend, tg_id: int, days_ago: float = 10) -> str:
    await register_user(backend, tg_id)
    email = await insert_key(
        backend, tg_id, tariff_id=STANDARD_TARIFF_ID, expiry_time=int(time.time() * 1000 - days_ago * DAY_MS)
    )
    await backend.pool.execute(
        "INSERT INTO payments (payment_id, tg_id, amount, status, payment_type, succeeded_at, created_at)"
        " VALUES ($1, $2, 199, 'succeeded', $3, NOW() - INTERVAL '40 days', NOW() - INTERVAL '40 days')",
        f"old-{tg_id}", tg_id, f"create_key|{STANDARD_TARIFF_ID}",
    )
    await backend.reload_cache()
    return email


async def _run(backend, tg_id: int):
    """Один проход воронки по пользователю, как в FunnelManager.run_cycle."""
    from services.notifications.funnels.winback_offer import WinbackOfferFunnel
    from services.notifications.models import NotificationContext

    class _NoWait:
        async def acquire(self, tg_id):
            return None

    service_data = backend.app.state.service_data
    user = await service_data.users.get_data(tg_id)
    keys = [k for k in await service_data.keys.get_all() if k.tg_id == tg_id]
    funnel = WinbackOfferFunnel(pool=backend.pool, rate_limiter=_NoWait())
    ctx = NotificationContext(user=user, keys=keys, segment_keys=[])
    if await funnel.should_send(ctx):
        return await funnel.process(ctx)
    return None


def _messages(backend, tg_id):
    return [json.dumps(m["params"], ensure_ascii=False) for m in backend.fakes.sent_messages(tg_id)]


async def test_lapsed_payer_gets_personal_code_that_works_once(backend, winback):
    email = await _paid_user_with_lapsed_key(backend, 8101)

    result = await _run(backend, 8101)

    assert result is not None and result.sent == 1
    promo = await backend.pool.fetchrow("SELECT * FROM promo_codes WHERE owner_tg_id = 8101")
    assert promo["max_redemptions"] == 1 and promo["discount_percent"] == winback.winback_discount_percent
    [message] = _messages(backend, 8101)
    assert promo["code"] in message
    assert f"promo={promo['code']}" in message and "renew=" in message

    from app.core.miniapp_session import web_sessions

    token, _ = web_sessions.issue(8101)
    quote = await backend.client.post(
        "/api/v1/web/payments/quotes",
        json={"tariff_id": STANDARD_TARIFF_ID, "operation": "renew_key", "email": email, "promo_code": promo["code"]},
        headers={**WEB_HEADERS, "Cookie": f"web_session={token}"},
    )
    assert quote.json()["promo_status"] == "applied"

    # Второй цикл в тот же день ничего не шлёт и новый код не выпускает
    assert await _run(backend, 8101) is None
    assert await backend.pool.fetchval("SELECT COUNT(*) FROM promo_codes WHERE owner_tg_id = 8101") == 1


async def test_control_group_gets_reminder_without_code(backend, winback, monkeypatch):
    monkeypatch.setattr(winback, "winback_control_percent", 100)
    await _paid_user_with_lapsed_key(backend, 8102)

    result = await _run(backend, 8102)

    assert result.sent == 1
    assert await backend.pool.fetchval("SELECT COUNT(*) FROM promo_codes") == 0
    offer = await backend.pool.fetchrow("SELECT * FROM winback_offers WHERE tg_id = 8102")
    assert offer["cohort"] == "control" and offer["promo_id"] is None


@pytest.mark.parametrize("days_ago", [2, 90])
async def test_outside_window_not_targeted(backend, winback, days_ago):
    await _paid_user_with_lapsed_key(backend, 8103, days_ago=days_ago)

    assert await _run(backend, 8103) is None
    assert backend.fakes.sent_messages(8103) == []


async def test_user_with_active_key_not_targeted(backend, winback):
    await _paid_user_with_lapsed_key(backend, 8104)
    await insert_key(backend, 8104, tariff_id=STANDARD_TARIFF_ID, expiry_time=int(time.time() * 1000 + DAY_MS))
    await backend.reload_cache()

    assert await _run(backend, 8104) is None


async def test_trial_only_user_not_targeted(backend, winback):
    await register_user(backend, 8105)
    await insert_key(backend, 8105, tariff_id=STANDARD_TARIFF_ID, expiry_time=int(time.time() * 1000 - 10 * DAY_MS))
    await backend.reload_cache()

    assert await _run(backend, 8105) is None


async def test_disabled_by_default(backend):
    await _paid_user_with_lapsed_key(backend, 8106)

    assert await _run(backend, 8106) is None


async def test_stats_compare_offer_and_control(backend, winback, monkeypatch):
    await _paid_user_with_lapsed_key(backend, 8107)
    await _run(backend, 8107)
    monkeypatch.setattr(winback, "winback_control_percent", 100)
    await _paid_user_with_lapsed_key(backend, 8108)
    await _run(backend, 8108)
    # Вернулся только получивший код
    await backend.pool.execute(
        "INSERT INTO payments (payment_id, tg_id, amount, status, payment_type, succeeded_at)"
        " VALUES ('back-8107', 8107, 139.3, 'succeeded', 'renew_key|x', NOW())"
    )

    stats = (await backend.client.get("/api/v1/admin/winback/stats")).json()

    assert stats["enabled"] is True
    assert stats["offer"] == {"sent": 1, "returned": 1, "revenue": pytest.approx(139.3)}
    assert stats["control"]["sent"] == 1 and stats["control"]["returned"] == 0
