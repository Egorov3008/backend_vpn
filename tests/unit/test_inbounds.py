"""Tests for inbound-set helpers.

Assertions are env-derived (read from `config`) so they hold against the real
repo-root .env, not hardcoded to any particular inbound list.
"""
from unittest.mock import MagicMock

from config import DEFAULT_PRICING_PLAN, LIST_AVAILABLE_CONNECTIONS, settings

from services.core.keys.utils import inbounds as ib

TRIAL_ID = int(DEFAULT_PRICING_PLAN)


def test_baseline_and_overlay():
    expected_baseline = (
        [settings.xui_inbound_id_landing] if settings.xui_inbound_id_landing else []
    )
    assert ib.BASELINE_INBOUNDS == expected_baseline
    assert ib.PAID_OVERLAY_INBOUNDS == list(LIST_AVAILABLE_CONNECTIONS)


def test_paid_inbound_ids():
    expected_baseline = (
        [settings.xui_inbound_id_landing] if settings.xui_inbound_id_landing else []
    )
    expected_paid = []
    seen = set()
    for i in expected_baseline + list(LIST_AVAILABLE_CONNECTIONS):
        if i not in seen:
            seen.add(i)
            expected_paid.append(int(i))
    assert ib.paid_inbound_ids() == expected_paid


def test_is_subscription_paid_and_trial():
    paid = MagicMock(id=5, amount=100.0)
    trial = MagicMock(id=TRIAL_ID, amount=0.0)
    free = MagicMock(id=2, amount=0.0)
    assert ib.is_subscription(paid) is True
    assert ib.is_subscription(trial) is True
    assert ib.is_subscription(free) is False
