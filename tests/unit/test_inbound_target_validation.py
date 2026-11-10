"""Валидация target и границ approve/revoke для утверждённых inbound'ов."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.core.panel.approved import (
    ApprovedInboundsService,
    InboundNotOnPanel,
    InvalidSyncTarget,
    LastApprovedInbound,
    NoApprovedInbounds,
)


def _cache(approved_ids):
    cache = MagicMock()
    cache.approved_inbounds.all = AsyncMock(
        return_value=[
            SimpleNamespace(inbound_id=i, sort_order=n)
            for n, i in enumerate(approved_ids)
        ]
    )
    cache.approved_inbounds.clear = AsyncMock()
    cache.approved_inbounds.set = AsyncMock()
    return cache


def _xui(panel_ids):
    xui = MagicMock()
    xui.get_inbounds = AsyncMock(
        return_value=[{"id": i, "remark": f"ib{i}", "protocol": "vless", "enable": True} for i in panel_ids]
    )
    return xui


def _repo(rows=(), remove_ok=True):
    repo = MagicMock()
    repo.list_ordered = AsyncMock(return_value=list(rows))
    repo.get = AsyncMock(return_value=SimpleNamespace(inbound_id=11))
    repo.add = AsyncMock(return_value=(SimpleNamespace(inbound_id=11, sort_order=0), True))
    repo.remove_if_not_last = AsyncMock(return_value=remove_ok)
    return repo


def _service(approved_ids=(11, 12), panel_ids=(11, 12), **repo_kwargs):
    return ApprovedInboundsService(_repo(**repo_kwargs), _xui(panel_ids)), _cache(approved_ids)


@pytest.mark.asyncio
async def test_default_target_is_approved_list():
    service, cache = _service()
    assert await service.resolve_target(cache, None) == [11, 12]


@pytest.mark.asyncio
async def test_target_outside_approved_rejected():
    service, cache = _service()
    with pytest.raises(InvalidSyncTarget) as exc:
        await service.resolve_target(cache, [11, 99])
    assert exc.value.invalid_inbound_ids == [99]
    assert exc.value.approved_inbound_ids == [11, 12]


@pytest.mark.asyncio
async def test_target_absent_on_panel_rejected():
    service, cache = _service(panel_ids=(11,))
    with pytest.raises(InvalidSyncTarget) as exc:
        await service.resolve_target(cache, [11, 12])
    assert exc.value.invalid_inbound_ids == [12]


@pytest.mark.asyncio
async def test_empty_target_rejected():
    service, cache = _service()
    with pytest.raises(InvalidSyncTarget):
        await service.resolve_target(cache, [])


@pytest.mark.asyncio
async def test_no_approved_inbounds_rejected():
    service, cache = _service(approved_ids=())
    with pytest.raises(NoApprovedInbounds):
        await service.resolve_target(cache, None)


@pytest.mark.asyncio
async def test_target_deduplicated_in_order():
    service, cache = _service()
    assert await service.resolve_target(cache, [12, 11, 12]) == [12, 11]


@pytest.mark.asyncio
async def test_approve_rejects_inbound_absent_on_panel():
    service, cache = _service(panel_ids=(11, 12))
    with pytest.raises(InboundNotOnPanel):
        await service.approve(MagicMock(), cache, 42, admin_tg_id=1)
    service.repo.add.assert_not_awaited()


@pytest.mark.asyncio
async def test_approve_persists_and_reloads_cache():
    service, cache = _service()
    _, created = await service.approve(MagicMock(), cache, 11, admin_tg_id=7)
    assert created is True
    service.repo.add.assert_awaited_once()
    cache.approved_inbounds.clear.assert_awaited_once()


@pytest.mark.asyncio
async def test_revoke_unknown_inbound_returns_false():
    service, cache = _service()
    service.repo.get = AsyncMock(return_value=None)
    assert await service.revoke(MagicMock(), cache, 11) is False


@pytest.mark.asyncio
async def test_revoke_last_inbound_rejected():
    service, cache = _service(remove_ok=False)
    with pytest.raises(LastApprovedInbound):
        await service.revoke(MagicMock(), cache, 11)


@pytest.mark.asyncio
async def test_list_panel_marks_approved_and_missing():
    service, cache = _service(approved_ids=(11, 77), panel_ids=(11, 12))
    result = await service.list_panel(cache)
    flags = {item["inbound_id"]: item["approved"] for item in result["inbounds"]}
    assert flags == {11: True, 12: False}
    assert result["approved_ids"] == [11, 77]
    assert result["missing_approved_ids"] == [77]
