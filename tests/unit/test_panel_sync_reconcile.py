import pytest
from unittest.mock import AsyncMock, MagicMock

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


@pytest.mark.asyncio
async def test_sync_data_runs_grace_reconcile_and_reports_count():
    sd = MagicMock()
    sd.keys.get_all = AsyncMock(return_value=[
        MagicMock(grace_expiry=10**13, expiry_time=10**13 + 1, email="a@x.c"),
        MagicMock(grace_expiry=None, email="b@x.c"),
    ])
    sd.cache_service.keys.all = AsyncMock(return_value=[])

    fetcher = MagicMock()
    fetcher.extract_clients = AsyncMock(return_value=[])
    sync = _build_sync(sd, fetcher)

    reconciled = []
    grace = MagicMock()
    grace.reconcile = AsyncMock(side_effect=lambda k: reconciled.append(k.email) or True)

    import services.synchron.database_synchronizer as M
    M._build_grace_manager = lambda *a, **kw: grace

    stats = await sync.sync_data(xui_session=MagicMock())

    assert reconciled == ["a@x.c"]  # only the subscription key
    assert stats.get("grace_reconciled") == 1


@pytest.mark.asyncio
async def test_reconcile_per_key_failure_does_not_abort_loop():
    """A reconcile exception on one key is logged; subsequent keys still reconcile
    and are counted."""
    import services.synchron.database_synchronizer as M

    sd = MagicMock()
    sd.keys.get_all = AsyncMock(return_value=[
        MagicMock(grace_expiry=10**13, expiry_time=10**13 + 1, email="boom@x.c"),
        MagicMock(grace_expiry=10**13, expiry_time=10**13 + 1, email="ok@x.c"),
    ])
    sd.cache_service.keys.all = AsyncMock(return_value=[])
    fetcher = MagicMock()
    fetcher.extract_clients = AsyncMock(return_value=[])
    sync = _build_sync(sd, fetcher)

    reconciled = []

    async def reconcile(key):
        if key.email == "boom@x.c":
            raise RuntimeError("panel flake")
        reconciled.append(key.email)
        return True

    grace = MagicMock()
    grace.reconcile = AsyncMock(side_effect=reconcile)
    M._build_grace_manager = lambda *a, **kw: grace

    stats = await sync.sync_data(xui_session=MagicMock())

    assert reconciled == ["ok@x.c"]
    assert stats.get("grace_reconciled") == 1


@pytest.mark.asyncio
async def test_reconcile_get_all_failure_does_not_abort_sync():
    """If keys.get_all raises, the reconcile pass logs and sync_data continues
    (returns 0 reconciled, does not propagate)."""
    import services.synchron.database_synchronizer as M

    sd = MagicMock()
    sd.keys.get_all = AsyncMock(side_effect=RuntimeError("db down"))
    sd.cache_service.keys.all = AsyncMock(return_value=[])
    fetcher = MagicMock()
    fetcher.extract_clients = AsyncMock(return_value=[])
    sync = _build_sync(sd, fetcher)

    grace = MagicMock()
    grace.reconcile = AsyncMock()
    M._build_grace_manager = lambda *a, **kw: grace

    stats = await sync.sync_data(xui_session=MagicMock())  # must not raise

    grace.reconcile.assert_not_awaited()
    assert stats.get("grace_reconciled") == 0