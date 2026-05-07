"""Telegram Login Widget HMAC verification utilities.

Implements the verification protocol described in
https://core.telegram.org/widgets/login#checking-authorization

Usage::

    from app.core.telegram import verify_telegram_hash, TelegramHashError

    try:
        verify_telegram_hash(payload_dict, bot_token)
    except TelegramHashError as exc:
        # signature invalid or auth_date too old
        ...
"""

from __future__ import annotations

import hashlib
import hmac as hmac_lib
import time
from typing import Any

# Telegram declares auth_date valid for 24h (86400 seconds).
_AUTH_DATE_MAX_AGE = 86400


class TelegramHashError(ValueError):
    """Raised when Telegram login payload fails HMAC verification or is expired."""


def verify_telegram_hash(data: dict[str, Any], bot_token: str) -> None:
    """Verify Telegram Login Widget payload HMAC signature and freshness.

    Mutates ``data`` by removing the ``hash`` field. Pass ``data.copy()`` if
    the original payload must be preserved.

    Args:
        data: Payload received from Telegram Login Widget. Must contain
            ``hash`` and ``auth_date`` fields plus any other fields used to
            compute the signature.
        bot_token: Telegram bot token. Its SHA-256 digest is used as the HMAC
            secret per Telegram's protocol.

    Raises:
        TelegramHashError: if the ``hash`` field is missing, the computed
            signature does not match (timing-attack-safe via
            :func:`hmac.compare_digest`), or ``auth_date`` is older than
            24 hours.
    """
    received_hash = data.pop("hash", None)
    if not received_hash:
        raise TelegramHashError("Missing hash field")

    secret = hashlib.sha256(bot_token.encode()).digest()
    items = sorted((k, str(v)) for k, v in data.items() if v is not None)
    check_string = "\n".join(f"{k}={v}" for k, v in items)
    expected = hmac_lib.new(
        secret, check_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac_lib.compare_digest(expected, received_hash):
        raise TelegramHashError("Invalid Telegram hash")

    auth_date = data.get("auth_date", 0)
    if time.time() - int(auth_date) > _AUTH_DATE_MAX_AGE:
        raise TelegramHashError("Auth data expired")
