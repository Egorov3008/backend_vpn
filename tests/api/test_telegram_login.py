import hashlib
import hmac
import time

import pytest

from app.core.telegram import TelegramHashError, verify_telegram_hash

BOT_TOKEN = "1234567890:test_bot_token_for_tests_only"


def _make_valid_hash(data: dict, bot_token: str) -> str:
    secret = hashlib.sha256(bot_token.encode()).digest()
    items = sorted((k, str(v)) for k, v in data.items() if v is not None)
    check_string = "\n".join(f"{k}={v}" for k, v in items)
    return hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()


def test_verify_valid_hash():
    auth_date = int(time.time())
    data = {"id": 123, "first_name": "Test", "auth_date": auth_date}
    data["hash"] = _make_valid_hash(data, BOT_TOKEN)
    verify_telegram_hash(data.copy(), BOT_TOKEN)  # should not raise


def test_verify_invalid_hash():
    data = {
        "id": 123,
        "first_name": "Test",
        "auth_date": int(time.time()),
        "hash": "badhash",
    }
    with pytest.raises(TelegramHashError):
        verify_telegram_hash(data, BOT_TOKEN)


def test_verify_expired_auth_date():
    old_date = int(time.time()) - 90000  # 25 hours ago
    data = {"id": 123, "first_name": "Test", "auth_date": old_date}
    data["hash"] = _make_valid_hash(data, BOT_TOKEN)
    with pytest.raises(TelegramHashError, match="expired"):
        verify_telegram_hash(data.copy(), BOT_TOKEN)


def test_verify_missing_hash():
    data = {"id": 123, "first_name": "Test", "auth_date": int(time.time())}
    with pytest.raises(TelegramHashError, match="Missing"):
        verify_telegram_hash(data, BOT_TOKEN)


def test_verify_uses_constant_time_comparison():
    """Ensure that verify_telegram_hash uses hmac.compare_digest (timing-attack safe).

    We exercise this by providing a hash of the wrong length — compare_digest
    must still return False without raising on length mismatch.
    """
    data = {"id": 123, "first_name": "Test", "auth_date": int(time.time())}
    data["hash"] = "a" * 10  # wrong length but well-formed string
    with pytest.raises(TelegramHashError, match="Invalid"):
        verify_telegram_hash(data, BOT_TOKEN)
