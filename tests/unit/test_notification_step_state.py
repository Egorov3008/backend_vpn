import json
import pytest
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

from services.notifications.step_state import StepState, MAX_STEPS


@pytest.fixture
def conn():
    c = MagicMock()
    c.fetchrow = AsyncMock(return_value=None)
    c.execute = AsyncMock(return_value=None)
    return c


@pytest.fixture
def pool(conn):
    p = MagicMock()
    p.acquire = MagicMock(return_value=conn)
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=None)
    return p


@pytest.mark.asyncio
async def test_get_state_returns_none_when_no_row(pool, conn):
    conn.fetchrow = AsyncMock(return_value=None)
    s = StepState(pool)
    assert await s.get_state("f1", 123) is None
    # fetchrow(sql, key) — ключ как параметр
    assert conn.fetchrow.call_args.args[1] == "notif_f1_123"


@pytest.mark.asyncio
async def test_get_state_returns_dict(pool, conn):
    # asyncpg возвращает jsonb как JSON-строку — реалистичный мок.
    conn.fetchrow = AsyncMock(
        return_value={"value": json.dumps({"step": 2, "last_sent_ms": 1000, "key_email": "a@x.c"})}
    )
    s = StepState(pool)
    state = await s.get_state("f1", 123)
    assert state == {"step": 2, "last_sent_ms": 1000, "key_email": "a@x.c"}


@pytest.mark.asyncio
async def test_advance_step_from_empty(pool, conn):
    conn.fetchrow = AsyncMock(return_value=None)
    s = StepState(pool)
    new_step = await s.advance_step(
        "f1", 123, last_sent_ms=9999, key_email="a@x.c", ttl=timedelta(days=30)
    )
    assert new_step == 1
    sql, key, value_json, _expires = conn.execute.call_args.args
    assert "notif_f1_123" in key
    parsed = json.loads(value_json)
    assert parsed == {"step": 1, "last_sent_ms": 9999, "key_email": "a@x.c"}
    assert "ON CONFLICT (key) DO UPDATE" in sql


@pytest.mark.asyncio
async def test_advance_step_increments_existing(pool, conn):
    conn.fetchrow = AsyncMock(
        return_value={"value": json.dumps({"step": 1, "last_sent_ms": 1000, "key_email": "old@x.c"})}
    )
    s = StepState(pool)
    new_step = await s.advance_step(
        "f1", 123, last_sent_ms=2000, key_email="new@x.c", ttl=timedelta(days=30)
    )
    assert new_step == 2
    parsed = json.loads(conn.execute.call_args.args[2])
    assert parsed == {"step": 2, "last_sent_ms": 2000, "key_email": "new@x.c"}


@pytest.mark.asyncio
async def test_mark_terminal_writes_max_step(pool, conn):
    s = StepState(pool)
    await s.mark_terminal("f1", 123, ttl=timedelta(days=30))
    parsed = json.loads(conn.execute.call_args.args[2])
    assert parsed == {"step": MAX_STEPS, "terminal": True}