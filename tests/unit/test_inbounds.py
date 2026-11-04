"""Tests for inbound-set helpers.

The approved set lives in the cache (DB after the first approval); the env list
is only a seed/fallback. Assertions are env-derived (read from `config`) so they
hold against the real repo-root .env, not hardcoded to any particular list.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from config import DEFAULT_PRICING_PLAN, LIST_AVAILABLE_CONNECTIONS

from services.core.keys.utils import inbounds as ib

TRIAL_ID = int(DEFAULT_PRICING_PLAN)


def _cache(rows):
    cache = MagicMock()
    cache.approved_inbounds.all = AsyncMock(return_value=list(rows))
    return cache


def test_overlay_is_available_connections():
    assert ib.PAID_OVERLAY_INBOUNDS == list(LIST_AVAILABLE_CONNECTIONS)


def test_env_inbound_ids_dedup_keeps_order():
    expected = []
    seen = set()
    for i in list(LIST_AVAILABLE_CONNECTIONS):
        if i not in seen:
            seen.add(i)
            expected.append(int(i))
    assert ib.env_inbound_ids() == expected


@pytest.mark.asyncio
async def test_approved_inbound_ids_empty_cache_falls_back_to_env():
    assert await ib.approved_inbound_ids(_cache([])) == ib.env_inbound_ids()


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
