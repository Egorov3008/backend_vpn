import pytest
from unittest.mock import AsyncMock, MagicMock

from background.scheduler import SyncScheduler


@pytest.mark.asyncio
async def test_grace_transitions_reconciles_subscription_keys():
    sd = MagicMock()
    k_active = MagicMock(grace_expiry=10**13, expiry_time=10**13 + 1, email="a@x.c")
    k_grace = MagicMock(grace_expiry=10**13, expiry_time=10**13 - 1, email="b@x.c")
    k_none = MagicMock(grace_expiry=None, email="c@x.c")  # landing/free — skip
    sd.keys.get_all = AsyncMock(return_value=[k_active, k_grace, k_none])
    pool = MagicMock()

    sched = SyncScheduler(service_data=sd, pool=pool)
    reconciled = []
    grace = MagicMock()
    grace.reconcile = AsyncMock(side_effect=lambda k: reconciled.append(k.email) or True)

    # Patch the module-level factory used inside run_grace_transitions:
    import background.scheduler as S
    S._build_grace_manager = lambda *a, **kw: grace

    await sched.run_grace_transitions()

    assert reconciled == ["a@x.c", "b@x.c"]  # k_none skipped
    grace.reconcile.assert_awaited()


@pytest.mark.asyncio
async def test_grace_transitions_no_keys_no_error():
    sd = MagicMock()
    sd.keys.get_all = AsyncMock(return_value=[])
    sched = SyncScheduler(service_data=sd, pool=MagicMock())
    import background.scheduler as S
    S._build_grace_manager = lambda *a, **kw: MagicMock(reconcile=AsyncMock())
    await sched.run_grace_transitions()  # must not raise


@pytest.mark.asyncio
async def test_grace_transitions_factory_called_with_scheduler_state():
    """The factory must receive (service_data, pool) in the right order —
    guards against a silent arg swap (the *a patch would mask it)."""
    import background.scheduler as S

    sd = MagicMock()
    k = MagicMock(grace_expiry=10**13, expiry_time=10**13 + 1, email="a@x.c")
    sd.keys.get_all = AsyncMock(return_value=[k])
    pool = MagicMock()
    sched = SyncScheduler(service_data=sd, pool=pool)

    captured = []
    grace = MagicMock()
    grace.reconcile = AsyncMock(return_value=True)

    def fake_factory(service_data, pool):
        captured.append((service_data, pool))
        return grace

    S._build_grace_manager = fake_factory
    await sched.run_grace_transitions()

    assert captured == [(sd, pool)]


@pytest.mark.asyncio
async def test_grace_transitions_get_all_failure_logs_and_returns():
    """If keys.get_all raises, the job logs and returns without reconciling."""
    import background.scheduler as S

    sd = MagicMock()
    sd.keys.get_all = AsyncMock(side_effect=RuntimeError("db down"))
    sched = SyncScheduler(service_data=sd, pool=MagicMock())

    grace = MagicMock()
    grace.reconcile = AsyncMock()
    S._build_grace_manager = lambda *a, **kw: grace

    await sched.run_grace_transitions()  # must not raise
    grace.reconcile.assert_not_awaited()


@pytest.mark.asyncio
async def test_grace_transitions_per_key_failure_does_not_abort_loop():
    """A reconcile exception on one key is logged; subsequent keys still reconcile."""
    import background.scheduler as S

    sd = MagicMock()
    k_boom = MagicMock(grace_expiry=10**13, expiry_time=10**13 + 1, email="boom@x.c")
    k_ok = MagicMock(grace_expiry=10**13, expiry_time=10**13 + 1, email="ok@x.c")
    sd.keys.get_all = AsyncMock(return_value=[k_boom, k_ok])
    sched = SyncScheduler(service_data=sd, pool=MagicMock())

    reconciled = []

    async def reconcile(key):
        if key.email == "boom@x.c":
            raise RuntimeError("panel flake")
        reconciled.append(key.email)
        return True

    grace = MagicMock()
    grace.reconcile = AsyncMock(side_effect=reconcile)
    S._build_grace_manager = lambda *a, **kw: grace

    await sched.run_grace_transitions()

    assert reconciled == ["ok@x.c"]  # loop continued past the failure