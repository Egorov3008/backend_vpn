"""Tests for inbound-set helpers (grace model).

Assertions are env-derived (read from `config`) so they hold against the real
repo-root .env, not hardcoded to any particular inbound list.
"""
from unittest.mock import MagicMock

from config import LIST_AVAILABLE_CONNECTIONS, settings

from services.core.keys.utils import inbounds as ib


def test_baseline_and_overlay():
    expected_baseline = (
        [settings.xui_inbound_id_landing] if settings.xui_inbound_id_landing else []
    )
    assert ib.BASELINE_INBOUNDS == expected_baseline
    assert ib.PAID_OVERLAY_INBOUNDS == list(LIST_AVAILABLE_CONNECTIONS)


def test_paid_grace_expired_sets():
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
    assert ib.grace_inbound_ids() == expected_baseline
    assert ib.expired_inbound_ids() == []


def test_expected_inbound_ids_by_status():
    expected_baseline = (
        [settings.xui_inbound_id_landing] if settings.xui_inbound_id_landing else []
    )
    seen = set()
    expected_paid = []
    for i in expected_baseline + list(LIST_AVAILABLE_CONNECTIONS):
        if i not in seen:
            seen.add(i)
            expected_paid.append(int(i))
    assert ib.expected_inbound_ids("active") == expected_paid
    assert ib.expected_inbound_ids("grace") == expected_baseline
    assert ib.expected_inbound_ids("expired") == []
    assert ib.expected_inbound_ids("none") == []


def test_is_subscription_paid_and_trial():
    paid = MagicMock(id=5, amount=100.0)
    trial = MagicMock(id=10, amount=0.0)
    free = MagicMock(id=2, amount=0.0)
    assert ib.is_subscription(paid) is True
    assert ib.is_subscription(trial) is True
    assert ib.is_subscription(free) is False


def test_grace_period_ms():
    assert ib.GRACE_PERIOD_DAYS == 7
    assert ib.GRACE_PERIOD_MS == 7 * 86_400_000