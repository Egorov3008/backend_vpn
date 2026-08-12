"""Tests for MaintenanceModeService (services/system/maintenance.py)."""

import json

import pytest

from services.system.maintenance import MaintenanceModeService


class FakeConn:
    def __init__(self, store: dict):
        self._store = store

    async def fetchrow(self, query, key):
        row = self._store.get(key)
        return {"value": json.dumps(row)} if row is not None else None

    async def execute(self, query, key, value):
        self._store[key] = json.loads(value)


class FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class FakePool:
    def __init__(self):
        self._store = {}

    def acquire(self):
        return FakeAcquire(FakeConn(self._store))


@pytest.mark.asyncio
async def test_get_status_default_disabled():
    pool = FakePool()
    service = MaintenanceModeService()

    status = await service.get_status(pool)

    assert status == {"enabled": False, "reason": None, "enabled_at": None, "enabled_by": None}


@pytest.mark.asyncio
async def test_is_enabled_false_by_default():
    pool = FakePool()
    service = MaintenanceModeService()

    assert await service.is_enabled(pool) is False


@pytest.mark.asyncio
async def test_set_enabled_true_persists_and_is_read_back():
    pool = FakePool()
    service = MaintenanceModeService()

    result = await service.set(pool, enabled=True, reason="плановые работы", admin_tg_id=42)

    assert result["enabled"] is True
    assert result["reason"] == "плановые работы"
    assert result["enabled_by"] == 42
    assert result["enabled_at"] is not None

    assert await service.is_enabled(pool) is True
    status = await service.get_status(pool)
    assert status["reason"] == "плановые работы"
    assert status["enabled_by"] == 42


@pytest.mark.asyncio
async def test_set_disabled_clears_reason_and_admin():
    pool = FakePool()
    service = MaintenanceModeService()

    await service.set(pool, enabled=True, reason="работы", admin_tg_id=42)
    result = await service.set(pool, enabled=False)

    assert result["enabled"] is False
    assert result["reason"] is None
    assert result["enabled_at"] is None
    assert result["enabled_by"] is None
    assert await service.is_enabled(pool) is False
