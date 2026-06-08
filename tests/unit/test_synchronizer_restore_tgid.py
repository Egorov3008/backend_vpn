"""
Regression-тест: при синхронизации панели с БД, если на панели tgId=0
но в БД tg_id > 0, синхронизатор должен восстановить tgId на панели
(источник истины для tg_id — наша БД).

Также проверяем, что при расхождении expiry_time приоритет у панели.
"""
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from client import PanelClient
from services.synchron.xui_fetcher import XUIFetcher
from services.synchron.cache_comparator import CacheComparator
from services.synchron.key_creator import KeyCreator
from services.synchron.traffic import TrafficUpdater


def _make_synchronizer():
    """Собрать DatabaseSynchronizer с моками."""
    model_data = MagicMock()
    model_data.keys = MagicMock()
    model_data.users = MagicMock()
    model_data.servers = MagicMock()
    pool = MagicMock()
    return XUIFetcher(), CacheComparator(), MagicMock(spec=KeyCreator), MagicMock(spec=TrafficUpdater), model_data, pool


def _build_synchronizer(xui_fetcher, cache_comparator, key_creator, traffic_updater, model_data, pool):
    from services.synchron.database_synchronizer import DatabaseSynchronizer
    return DatabaseSynchronizer(
        xui_fetcher=xui_fetcher,
        cache_comparator=cache_comparator,
        key_creator=key_creator,
        traffic_updater=traffic_updater,
        model_data=model_data,
        pool=pool,
    )


@pytest.mark.asyncio
async def test_sync_restores_tg_id_on_panel_when_db_has_it():
    """Когда tgId=0 на панели, но tg_id в БД > 0, должен вызваться update_standalone_client."""
    xui_fetcher, cache_comparator, key_creator, traffic_updater, model_data, pool = _make_synchronizer()

    # Клиент с панели: tgId=0, expiry_time=1783337068246
    panel_client = PanelClient(
        id="4a4d2919-bfbd-482e-9426-06800c27e22e",
        email="6cx7ah",
        tg_id=0,
        expiry_time=1783337068246,
        inbound_ids=[39, 51, 64],
    )
    xui_fetcher.extract_clients = AsyncMock(return_value=[panel_client])

    # В кэше (БД) ключ 6cx7ah с tg_id=397349989
    db_key = MagicMock()
    db_key.email = "6cx7ah"
    db_key.tg_id = 397349989
    model_data.keys.get_all = AsyncMock(return_value=[db_key])
    model_data.users.get_all = AsyncMock(return_value=[])
    model_data.keys.get_data = AsyncMock(return_value=db_key)
    model_data.servers.get_data = AsyncMock(return_value=None)
    cache_comparator.set_cache_data = AsyncMock()
    cache_comparator.set_panel_data = MagicMock()
    cache_comparator.compare = Mock(return_value=([], [], []))  # ничего не пропало

    key_creator.ensure_user_exists = AsyncMock(return_value=True)
    key_creator.create_key = AsyncMock(return_value=None)

    traffic_updater.fetch_traffic_batch = AsyncMock(return_value={})
    traffic_updater.update_key_with_traffic = AsyncMock(return_value=True)

    sync = _build_synchronizer(xui_fetcher, cache_comparator, key_creator, traffic_updater, model_data, pool)
    sync.get_client_session = AsyncMock()

    # XUI-сессия с замоканным update_standalone_client
    xui_session = MagicMock()
    xui_session.server_id = 2
    xui_session.update_standalone_client = AsyncMock(return_value={"success": True})

    result = await sync.sync_data(xui_session)

    # ГЛАВНОЕ: должен вызваться update_standalone_client('6cx7ah', tgId=397349989)
    xui_session.update_standalone_client.assert_awaited_once()
    call_args = xui_session.update_standalone_client.await_args
    assert call_args.args[0] == "6cx7ah"
    assert call_args.kwargs.get("tgId") == 397349989

    assert result["restored_tg_ids"] == 1


@pytest.mark.asyncio
async def test_sync_does_not_restore_tg_id_when_both_match():
    """Когда tg_id и БД и панели совпадают (или оба > 0), update НЕ вызывается."""
    xui_fetcher, cache_comparator, key_creator, traffic_updater, model_data, pool = _make_synchronizer()

    panel_client = PanelClient(
        id="uuid-1",
        email="ffoxhn",
        tg_id=383952206,
        expiry_time=1781349582904,
        inbound_ids=[39],
    )
    xui_fetcher.extract_clients = AsyncMock(return_value=[panel_client])

    db_key = MagicMock()
    db_key.email = "ffoxhn"
    db_key.tg_id = 383952206
    model_data.keys.get_all = AsyncMock(return_value=[db_key])
    model_data.users.get_all = AsyncMock(return_value=[])
    model_data.keys.get_data = AsyncMock(return_value=db_key)
    cache_comparator.set_cache_data = AsyncMock()
    cache_comparator.set_panel_data = MagicMock()
    cache_comparator.compare = Mock(return_value=([], [], []))

    traffic_updater.fetch_traffic_batch = AsyncMock(return_value={})
    traffic_updater.update_key_with_traffic = AsyncMock(return_value=True)
    model_data.servers.get_data = AsyncMock(return_value=None)

    sync = _build_synchronizer(xui_fetcher, cache_comparator, key_creator, traffic_updater, model_data, pool)
    sync.get_client_session = AsyncMock()

    xui_session = MagicMock()
    xui_session.server_id = 2
    xui_session.update_standalone_client = AsyncMock()

    result = await sync.sync_data(xui_session)

    xui_session.update_standalone_client.assert_not_awaited()
    assert result.get("restored_tg_ids", 0) == 0


@pytest.mark.asyncio
async def test_sync_panel_expiry_overrides_db_expiry():
    """expiry_time с панели имеет приоритет — обновляем БД из панели."""
    xui_fetcher, cache_comparator, key_creator, traffic_updater, model_data, pool = _make_synchronizer()

    # Панель: expiry = 1783337068246 (будущее)
    panel_client = PanelClient(
        id="uuid-1",
        email="ffoxhn",
        tg_id=383952206,
        expiry_time=1783337068246,
        inbound_ids=[39],
    )
    xui_fetcher.extract_clients = AsyncMock(return_value=[panel_client])

    # БД: expiry = 1700000000000 (прошлое — кто-то не обновил в БД)
    db_key = MagicMock()
    db_key.email = "ffoxhn"
    db_key.tg_id = 383952206
    db_key.expiry_time = 1700000000000
    model_data.keys.get_all = AsyncMock(return_value=[db_key])
    model_data.users.get_all = AsyncMock(return_value=[])
    model_data.keys.get_data = AsyncMock(return_value=db_key)
    model_data.servers.get_data = AsyncMock(return_value=None)
    cache_comparator.set_cache_data = AsyncMock()
    cache_comparator.set_panel_data = MagicMock()
    cache_comparator.compare = Mock(return_value=([], [], []))

    traffic_updater.fetch_traffic_batch = AsyncMock(return_value={})
    # update_key_with_traffic должен быть вызван с expiry_time ПАНЕЛИ
    traffic_updater.update_key_with_traffic = AsyncMock(return_value=True)

    sync = _build_synchronizer(xui_fetcher, cache_comparator, key_creator, traffic_updater, model_data, pool)
    sync.get_client_session = AsyncMock()

    xui_session = MagicMock()
    xui_session.server_id = 2
    xui_session.update_standalone_client = AsyncMock()

    await sync.sync_data(xui_session)

    # Проверяем, что в update_key_with_traffic передан panel_client с его expiry_time
    traffic_updater.update_key_with_traffic.assert_awaited_once()
    args = traffic_updater.update_key_with_traffic.await_args
    passed_client = args.args[2]  # (pool, key, client, traffic_data)
    assert passed_client.expiry_time == 1783337068246
