"""sync_data() больше не выполняет grace-reconcile проход (модель grace
удалена из бэкенда). Эти тесты проверяют, что sync_data работает без
него: нет ключа "grace_reconciled" в статистике, _build_grace_manager
не существует, и панель-синк не падает при отсутствии клиентов.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

import services.synchron.database_synchronizer as M
from services.synchron.database_synchronizer import DatabaseSynchronizer


def _build_sync(sd, fetcher):
    return DatabaseSynchronizer(
        xui_fetcher=fetcher,
        cache_comparator=MagicMock(),
        key_creator=MagicMock(),
        traffic_updater=MagicMock(),
        model_data=sd,
        pool=MagicMock(),
    )


def test_build_grace_manager_helper_removed():
    """Grace-reconcile helper удалён из модуля вместе с моделью grace."""
    assert not hasattr(M, "_build_grace_manager")


@pytest.mark.asyncio
async def test_sync_data_no_clients_has_no_grace_reconciled_key():
    """Ранний выход (панель без клиентов) не содержит grace_reconciled."""
    sd = MagicMock()
    sd.keys.get_all = AsyncMock(return_value=[])
    sd.cache_service.keys.all = AsyncMock(return_value=[])

    fetcher = MagicMock()
    fetcher.extract_clients = AsyncMock(return_value=[])
    sync = _build_sync(sd, fetcher)

    stats = await sync.sync_data(xui_session=MagicMock())

    assert "grace_reconciled" not in stats


@pytest.mark.asyncio
async def test_sync_data_error_path_has_no_grace_reconciled_key():
    """Ошибка синхронизации тоже не должна содержать grace_reconciled
    в возвращаемом словаре (ключ полностью удалён из обоих error-return)."""
    sd = MagicMock()
    fetcher = MagicMock()
    fetcher.extract_clients = AsyncMock(side_effect=RuntimeError("panel down"))
    sync = _build_sync(sd, fetcher)

    stats = await sync.sync_data(xui_session=MagicMock())

    assert "grace_reconciled" not in stats
    assert stats.get("error") == "panel down"
