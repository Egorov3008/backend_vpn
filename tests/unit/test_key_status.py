from unittest.mock import MagicMock
from services.core.keys.utils.status import KeyStatus


def _key(expiry, grace):
    k = MagicMock()
    k.expiry_time = expiry
    k.grace_expiry = grace
    return k


def test_none_when_no_key():
    assert KeyStatus.of(None, now_ms=0) == "none"


def test_none_when_no_expiry_time():
    assert KeyStatus.of(_key(None, 9000), now_ms=0) == "none"
    assert KeyStatus.of(_key(0, 9000), now_ms=0) == "none"


def test_active_before_expiry():
    assert KeyStatus.of(_key(2000, 9000), now_ms=1999) == "active"


def test_active_when_grace_expiry_missing_but_not_expired():
    # Legacy key without grace_expiry classifies by expiry_time alone, not NONE.
    assert KeyStatus.of(_key(2000, None), now_ms=1999) == "active"


def test_grace_between_expiry_and_grace_expiry():
    assert KeyStatus.of(_key(2000, 9000), now_ms=2000) == "grace"
    assert KeyStatus.of(_key(2000, 9000), now_ms=8999) == "grace"


def test_expired_at_grace_expiry():
    assert KeyStatus.of(_key(2000, 9000), now_ms=9000) == "expired"
    assert KeyStatus.of(_key(2000, 9000), now_ms=9999) == "expired"


def test_expired_when_grace_missing_and_expiry_passed():
    # Legacy key past expiry with no grace window is EXPIRED, not NONE.
    assert KeyStatus.of(_key(2000, None), now_ms=2000) == "expired"


def test_defaults_to_now():
    # now_ms defaults to current time; just ensure it runs without error.
    assert KeyStatus.of(_key(2000, 9000)) in ("active", "grace", "expired")