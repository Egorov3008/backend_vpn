"""Тесты endpoint /api/v1/admin/dashboard-metrics и DashboardMetricsService.

Портировано из web/app/services/dashboard_metrics.py — web больше не считает
эти метрики прямым SQL, а получает их отсюда (backend — единственный
владелец Postgres).
"""
from datetime import date
from unittest.mock import AsyncMock

import pytest

from services.admin_dashboard_metrics import DashboardMetricsService


class _Conn:
    """Фейк asyncpg-соединения: fetch возвращает строки по порядку вызовов."""

    def __init__(self, row_sets):
        self._row_sets = list(row_sets)
        self.queries = []

    async def fetch(self, sql, *args):
        self.queries.append(sql)
        return self._row_sets.pop(0) if self._row_sets else []


class _Pool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return self

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


MRR_ROWS = [
    {"month": date(2026, 8, 1), "revenue": 10000.0, "paying_users": 20, "arpu": 500.0},
    {"month": date(2026, 7, 1), "revenue": 8000.0, "paying_users": 16, "arpu": 500.0},
]
FUNNEL_ROWS = [
    {"date": date(2026, 8, 1), "new_users": 5, "users_with_keys": 3, "paying_users": 1},
]
EXPIRY_ROWS = [
    {"expiry_range": "Менее 24ч", "keys_count": 2},
    {"expiry_range": "Более 72ч", "keys_count": 10},
]
PAYMENT_ROWS = [
    {"status": "succeeded", "count": 30, "total_amount": 15000.0},
    {"status": "pending", "count": 5, "total_amount": 2000.0},
]


@pytest.mark.asyncio
async def test_dashboard_metrics_endpoint_returns_service_result(api_client, monkeypatch):
    expected = {
        "mrr_current_month": 10000.0, "mrr_previous_month": 8000.0, "mrr_growth": 25.0,
        "paying_users_current": 20, "arpu_current": 500.0,
        "funnel": [], "total_new_users_30d": 0, "total_users_with_keys_30d": 0,
        "total_paying_users_30d": 0, "conversion_to_keys_pct": 0.0, "conversion_to_paid_pct": 0.0,
        "expiring_keys": [], "total_expiring_72h": 0,
        "payment_statuses": [], "total_succeeded": 0, "total_pending": 0,
        "total_canceled": 0, "succeeded_pct": 0.0,
    }
    monkeypatch.setattr(
        DashboardMetricsService, "get_all_dashboard_metrics", AsyncMock(return_value=expected)
    )
    r = await api_client.get("/api/v1/admin/dashboard-metrics")
    assert r.status_code == 200
    assert r.json() == expected


@pytest.mark.asyncio
async def test_service_aggregates_all_sections():
    conn = _Conn([MRR_ROWS, FUNNEL_ROWS, EXPIRY_ROWS, PAYMENT_ROWS])
    service = DashboardMetricsService(_Pool(conn))
    result = await service.get_all_dashboard_metrics()

    assert result["mrr_current_month"] == 10000.0
    assert result["mrr_previous_month"] == 8000.0
    assert result["mrr_growth"] == 25.0
    assert result["paying_users_current"] == 20

    assert result["funnel"] == [
        {"date": "2026-08-01", "new_users": 5, "users_with_keys": 3, "paying_users": 1}
    ]
    assert result["total_new_users_30d"] == 5
    assert result["conversion_to_keys_pct"] == 60.0
    assert result["conversion_to_paid_pct"] == 20.0

    assert result["total_expiring_72h"] == 2  # "Более 72ч" excluded
    assert result["expiring_keys"][0]["expiry_range"] == "Менее 24ч"

    assert result["total_succeeded"] == 30
    assert result["total_pending"] == 5
    assert result["succeeded_pct"] == pytest.approx(30 / 35 * 100)

    assert len(conn.queries) == 4


@pytest.mark.asyncio
async def test_service_handles_empty_data():
    conn = _Conn([[], [], [], []])
    service = DashboardMetricsService(_Pool(conn))
    result = await service.get_all_dashboard_metrics()

    assert result["mrr_current_month"] == 0.0
    assert result["mrr_growth"] == 0.0
    assert result["funnel"] == []
    assert result["conversion_to_keys_pct"] == 0.0
    assert result["total_expiring_72h"] == 0
    assert result["succeeded_pct"] == 0.0


@pytest.mark.asyncio
async def test_service_propagates_db_error():
    class _BadPool(_Pool):
        def acquire(self):
            raise RuntimeError("connection refused")

    service = DashboardMetricsService(_BadPool(_Conn([])))
    with pytest.raises(RuntimeError):
        await service.get_all_dashboard_metrics()
