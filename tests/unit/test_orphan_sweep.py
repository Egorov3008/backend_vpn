"""Task 8: _sweep_orphan_keys — удаляет ключи, чей tg_id больше нет в users.

Возникают после admin_delete_user с частично неудачным XUI-удалением:
user удалён, key row остался (panel delete вернул False/упал). Без sweep
такие строки копятся как orphans — ключ без пользователя.

Reconcile-шаг: SELECT keys LEFT JOIN users WHERE u.tg_id IS NULL →
delete_client на панели + keys.delete в БД + cache.keys.delete.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from background.scheduler import SyncScheduler


@pytest.mark.asyncio
async def test_orphan_keys_swept():
    """Orphan keys (no matching user) удаляются из панели и БД; count возвращается."""
    sd = MagicMock()
    pool = MagicMock()
    conn = AsyncMock()
    rows = [
        {"email": "a@b.com", "inbound_id": 1, "client_id": "c1"},
        {"email": "c@d.com", "inbound_id": 2, "client_id": "c2"},
    ]
    conn.fetch = AsyncMock(return_value=rows)
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    sd.data_service.keys.delete = AsyncMock()
    sd.cache_service.keys.delete = AsyncMock()

    sched = SyncScheduler(sd, pool)

    # _sweep_orphan_keys строит xui через `from client import XUISession`.
    # Патчим по месту импорта.
    with patch("client.XUISession") as X:
        xui = MagicMock()
        xui.delete_client = AsyncMock(return_value=True)
        X.return_value = xui
        result = await sched._sweep_orphan_keys()

    assert result["orphan_keys_removed"] == 2
    # Каждый orphan удалён из БД и кэша ровно один раз.
    assert sd.data_service.keys.delete.call_count == 2
    assert sd.cache_service.keys.delete.call_count == 2
    # delete_client вызван с (email, inbound_id, client_id) для каждой строки.
    assert xui.delete_client.await_count == 2
    called_emails = {c.args[0] for c in xui.delete_client.await_args_list}
    assert called_emails == {"a@b.com", "c@d.com"}


@pytest.mark.asyncio
async def test_orphan_keys_none_when_no_rows():
    """Нет orphans → 0 удалений, delete_client не вызывается."""
    sd = MagicMock()
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    sd.data_service.keys.delete = AsyncMock()
    sd.cache_service.keys.delete = AsyncMock()

    sched = SyncScheduler(sd, pool)

    with patch("client.XUISession") as X:
        xui = MagicMock()
        xui.delete_client = AsyncMock(return_value=True)
        X.return_value = xui
        result = await sched._sweep_orphan_keys()

    assert result["orphan_keys_removed"] == 0
    xui.delete_client.assert_not_awaited()
    sd.data_service.keys.delete.assert_not_called()


@pytest.mark.asyncio
async def test_orphan_keys_panel_delete_failure_skips_db_delete():
    """Если xui.delete_client вернул False — БД/кэш не трогаем, count не растёт.

    Orphan остаётся для повторной попытки следующим sweep'ом (идемпотентность).
    """
    sd = MagicMock()
    pool = MagicMock()
    conn = AsyncMock()
    rows = [{"email": "a@b.com", "inbound_id": 1, "client_id": "c1"}]
    conn.fetch = AsyncMock(return_value=rows)
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    sd.data_service.keys.delete = AsyncMock()
    sd.cache_service.keys.delete = AsyncMock()

    sched = SyncScheduler(sd, pool)

    with patch("client.XUISession") as X:
        xui = MagicMock()
        xui.delete_client = AsyncMock(return_value=False)
        X.return_value = xui
        result = await sched._sweep_orphan_keys()

    assert result["orphan_keys_removed"] == 0
    sd.data_service.keys.delete.assert_not_called()
    sd.cache_service.keys.delete.assert_not_called()