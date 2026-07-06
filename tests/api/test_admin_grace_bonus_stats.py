"""Тесты endpoint /api/v1/admin/grace-bonus-stats и сервиса GraceBonusStatsService."""
from unittest.mock import AsyncMock

import pytest

from services.grace_bonus_stats import GraceBonusStatsService


EXPECTED = {
    "grace": {
        "currently_in_grace": 3,
        "expired_after_grace": {"cumulative": 10, "today": 1, "yesterday": 2},
        "renewed_from_grace": {"cumulative": 5, "today": 0, "yesterday": 1},
    },
    "channel_bonus": {"cumulative": 7, "today": 1, "yesterday": 0},
}


class _Conn:
    """Фейк asyncpg-соединения: fetchval возвращает значения по порядку."""

    def __init__(self, values):
        self._values = list(values)
        self.queries = []
        self.calls = []

    async def fetchval(self, sql, *args):
        self.queries.append(sql)
        self.calls.append(args)
        return self._values.pop(0) if self._values else 0


class _Pool:
    """Фейк пула: `async with pool.acquire() as conn` → _Conn."""

    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return self

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_grace_bonus_stats_endpoint_returns_service_result(api_client, monkeypatch):
    """Endpoint отдаёт результат сервиса и проходит auth-override."""
    monkeypatch.setattr(
        GraceBonusStatsService, "get", AsyncMock(return_value=EXPECTED)
    )
    r = await api_client.get("/api/v1/admin/grace-bonus-stats")
    assert r.status_code == 200
    assert r.json() == EXPECTED


@pytest.mark.asyncio
async def test_service_aggregates_in_expected_order():
    """fetchval вызывается 10 раз в порядке метрик; ответ собирается корректно."""
    # порядок: currently_in_grace, expired_cum, expired_today, expired_yesterday,
    # renewed_cum, renewed_today, renewed_yesterday, bonus_cum, bonus_today, bonus_yesterday
    conn = _Conn([3, 10, 1, 2, 5, 0, 1, 7, 1, 0])
    service = GraceBonusStatsService(_Pool(conn))
    result = await service.get()

    assert result == EXPECTED
    # 10 запросов, все с COUNT(*)
    assert len(conn.queries) == 10
    assert all("COUNT(*)" in q for q in conn.queries)
    # ключевые таблицы присутствуют
    joined = "\n".join(conn.queries)
    assert "FROM keys" in joined
    assert "FROM grace_renewal_log" in joined
    assert "FROM user_promo_claims" in joined
    # promo_id передан параметром $1, не inline — проверяем аргумент вызова
    promo_calls = [c for c in conn.calls if c and c[0] == "channel_subscription_bonus"]
    assert len(promo_calls) == 3  # cumulative + today + yesterday


@pytest.mark.asyncio
async def test_service_propagates_db_error():
    """Сбой БД пробрасывается (endpoint вернёт 500), без молчаливых нулей."""
    class _BadPool(_Pool):
        def acquire(self):
            raise RuntimeError("connection refused")

    service = GraceBonusStatsService(_BadPool(_Conn([])))
    with pytest.raises(RuntimeError):
        await service.get()