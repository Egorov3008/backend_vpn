"""Task 8: _sweep_orphan_keys — обнаруживает ключи, чей tg_id больше нет в users.

Возникают после admin_delete_user с частично неудачным XUI-удалением:
user удалён, key row остался (panel delete вернул False/упал). Sweep — только
диагностика: панель/БД/кэш НЕ трогает, только логирует найденные orphans для
последующего ручного удаления админом (admin_delete_key/admin_delete_user).
Автоматическое удаление с панели без участия админа запрещено.

Reconcile-шаг: SELECT keys LEFT JOIN users WHERE u.tg_id IS NULL → лог + count.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from background.scheduler import SyncScheduler


@pytest.mark.asyncio
async def test_orphan_keys_found_but_not_deleted():
    """Orphan keys (no matching user) обнаруживаются, но НЕ удаляются
    автоматически из панели/БД/кэша — только count возвращается."""
    sd = MagicMock()
    pool = MagicMock()
    conn = AsyncMock()
    rows = [
        {"email": "a@b.com"},
        {"email": "c@d.com"},
    ]
    conn.fetch = AsyncMock(return_value=rows)
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    sd.data_service.keys.delete = AsyncMock()
    sd.cache_service.keys.delete = AsyncMock()

    sched = SyncScheduler(sd, pool)

    result = await sched._sweep_orphan_keys()

    assert result["orphan_keys_found"] == 2
    # Ничего не удаляется автоматически — ни из панели, ни из БД, ни из кэша.
    sd.data_service.keys.delete.assert_not_called()
    sd.cache_service.keys.delete.assert_not_called()


@pytest.mark.asyncio
async def test_orphan_keys_none_when_no_rows():
    """Нет orphans → 0 найдено."""
    sd = MagicMock()
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    sd.data_service.keys.delete = AsyncMock()
    sd.cache_service.keys.delete = AsyncMock()

    sched = SyncScheduler(sd, pool)

    result = await sched._sweep_orphan_keys()

    assert result["orphan_keys_found"] == 0
    sd.data_service.keys.delete.assert_not_called()
    sd.cache_service.keys.delete.assert_not_called()


@pytest.mark.asyncio
async def test_orphan_keys_sweep_survives_db_error():
    """Ошибка при чтении БД не рушит sweep — возвращает 0, не поднимает исключение."""
    sd = MagicMock()
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetch = AsyncMock(side_effect=Exception("db unavailable"))
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    sched = SyncScheduler(sd, pool)

    result = await sched._sweep_orphan_keys()

    assert result["orphan_keys_found"] == 0
