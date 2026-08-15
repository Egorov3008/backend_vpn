from unittest.mock import MagicMock
from services.core.keys.utils.status import KeyStatus


def _key(expiry):
    k = MagicMock()
    k.expiry_time = expiry
    return k


def test_none_when_no_key():
    assert KeyStatus.of(None, now_ms=0) == "none"


def test_none_when_no_expiry_time():
    assert KeyStatus.of(_key(None), now_ms=0) == "none"
    assert KeyStatus.of(_key(0), now_ms=0) == "none"


def test_active_before_expiry():
    assert KeyStatus.of(_key(2000), now_ms=1999) == "active"


def test_expired_at_expiry():
    assert KeyStatus.of(_key(2000), now_ms=2000) == "expired"
    assert KeyStatus.of(_key(2000), now_ms=9999) == "expired"


def test_defaults_to_now():
    # now_ms defaults to current time; just ensure it runs without error.
    assert KeyStatus.of(_key(2000)) in ("active", "expired")
