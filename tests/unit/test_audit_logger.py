import pytest
from unittest.mock import AsyncMock, MagicMock

from services.admin_audit import AuditLogger


@pytest.mark.asyncio
async def test_record_inserts_row():
    conn = AsyncMock()
    conn.execute = AsyncMock()
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    await AuditLogger(pool).record(admin_tg_id=123, action="delete_key", target="a@b.com")
    conn.execute.assert_called_once()
    args = conn.execute.call_args.args
    assert "INSERT INTO admin_audit_log" in args[0]
    assert args[1] == 123 and args[2] == "delete_key" and args[3] == "a@b.com"


@pytest.mark.asyncio
async def test_record_swallows_insert_error():
    conn = AsyncMock()
    conn.execute = AsyncMock(side_effect=RuntimeError("db down"))
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    # не должно поднимать
    await AuditLogger(pool).record(admin_tg_id=123, action="delete_key", target="a@b.com")