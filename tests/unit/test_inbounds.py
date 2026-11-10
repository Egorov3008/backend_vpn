"""Tests for inbound-set helpers.

The approved set lives in the ``approved_inbounds`` table (mirrored into the
cache). Nothing falls back to env any more: an empty cache means no inbound
is approved, and callers must handle that.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from config import DEFAULT_PRICING_PLAN

from services.core.keys.utils import inbounds as ib

TRIAL_ID = int(DEFAULT_PRICING_PLAN)


def _cache(rows):
    cache = MagicMock()
    cache.approved_inbounds.all = AsyncMock(return_value=list(rows))
    return cache


@pytest.mark.asyncio
async def test_approved_inbound_ids_empty_cache_returns_empty():
    assert await ib.approved_inbound_ids(_cache([])) == []


@pytest.mark.asyncio
async def test_approved_inbound_ids_ordered_by_sort_order_and_deduped():
    rows = [
        SimpleNamespace(inbound_id=12, sort_order=1),
        SimpleNamespace(inbound_id=7, sort_order=0),
        SimpleNamespace(inbound_id=12, sort_order=2),
    ]
    assert await ib.approved_inbound_ids(_cache(rows)) == [7, 12]


@pytest.mark.asyncio
async def test_approved_inbound_ids_int_normalized():
    rows = [SimpleNamespace(inbound_id="9", sort_order=0)]
    assert await ib.approved_inbound_ids(_cache(rows)) == [9]


def test_is_subscription_paid_and_trial():
    paid = MagicMock(id=5, amount=100.0)
    trial = MagicMock(id=TRIAL_ID, amount=0.0)
    free = MagicMock(id=2, amount=0.0)
    assert ib.is_subscription(paid) is True
    assert ib.is_subscription(trial) is True
    assert ib.is_subscription(free) is False
