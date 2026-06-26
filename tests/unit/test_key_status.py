# backend/tests/unit/test_key_status.py
from unittest.mock import MagicMock
from services.core.keys.utils.status import KeyStatus


def _key(expiry, grace):
    k = MagicMock()
    k.expiry_time = expiry
    k.grace_expiry = grace
    return k


def test_none_when_grace_expiry_is_none():
    assert KeyStatus.of(_key(1000, None), now_ms=0) == "none"


def test_active_before_expiry():
    assert KeyStatus.of(_key(2000, 9000), now_ms=1999) == "active"


def test_grace_between_expiry_and_grace_expiry():
    assert KeyStatus.of(_key(2000, 9000), now_ms=2000) == "grace"
    assert KeyStatus.of(_key(2000, 9000), now_ms=8999) == "grace"


def test_expired_at_grace_expiry():
    assert KeyStatus.of(_key(2000, 9000), now_ms=9000) == "expired"
    assert KeyStatus.of(_key(2000, 9000), now_ms=9999) == "expired"


def test_defaults_to_now(monkeypatch):
    # now_ms defaults to current time; just ensure it runs without error.
    assert KeyStatus.of(_key(2000, 9000)) in ("active", "grace", "expired")