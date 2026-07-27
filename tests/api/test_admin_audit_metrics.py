"""P2-2: наблюдаемость аудита — admin_audit_failures_total.

AuditLogger fail-open глотает сбой вставки в admin_audit_log (операция не должна
рваться). До P2-2 сбой был виден только в warning-логе; теперь инкрементируется
prometheus-счётчик vpn_admin_audit_failures_total{action,error_type}.

Покрывает:
- сбой INSERT → record не падает (fail-open сохранён) + счётчик растёт.
- успешный INSERT → счётчик не трогается.
- error_type = type(e).__name__ попадает в label.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

import asyncpg

from services.admin_audit import AuditLogger
from services.metrics.registry import REGISTRY, admin_audit_failures_total


def _make_pool(conn):
    """pool, где `async with pool.acquire() as conn` отдаёт заданный conn-mock."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock(spec=asyncpg.Pool)
    pool.acquire = MagicMock(return_value=cm)
    return pool


def _counter_value(action: str, error_type: str) -> float:
    return REGISTRY.get_sample_value(
        "vpn_admin_audit_failures_total",
        {"action": action, "error_type": error_type},
    ) or 0.0


@pytest.mark.asyncio
async def test_audit_failure_increments_counter_and_stays_fail_open():
    """Сбой INSERT → record не падает, счётчик {action, error_type} растёт."""
    action, error_type = "delete_key", "RuntimeError"
    before = _counter_value(action, error_type)

    conn = AsyncMock()
    conn.execute = AsyncMock(side_effect=RuntimeError("db down"))
    pool = _make_pool(conn)

    # fail-open: исключение не должно вырваться наружу
    await AuditLogger(pool).record(7, action, "a@b.com")

    after = _counter_value(action, error_type)
    assert after > before, "счётчик сбоев аудита не вырос"


@pytest.mark.asyncio
async def test_audit_failure_labels_error_type_name():
    """error_type label = type(e).__name__ (не строка сообщения)."""
    action, error_type = "change_tariff", "ConnectionResetError"
    before = _counter_value(action, error_type)

    conn = AsyncMock()
    conn.execute = AsyncMock(side_effect=ConnectionResetError("gone"))
    pool = _make_pool(conn)

    await AuditLogger(pool).record(1, action, "x@y.com")

    assert _counter_value(action, error_type) > before


@pytest.mark.asyncio
async def test_audit_success_does_not_increment_counter():
    """Успешный INSERT не трогает счётчик сбоев."""
    action, error_type = "generate_key", "RuntimeError"
    before = _counter_value(action, error_type)

    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=None)  # успех
    pool = _make_pool(conn)

    await AuditLogger(pool).record(7, action, "9:2")

    assert _counter_value(action, error_type) == before