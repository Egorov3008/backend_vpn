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
import json
import time
from typing import Any
from urllib.parse import parse_qsl

# Telegram declares auth_date valid for 24h (86400 seconds).
_AUTH_DATE_MAX_AGE = 86400
# Допуск на расхождение часов для auth_date "из будущего".
_AUTH_DATE_FUTURE_SKEW = 60


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


def verify_webapp_init_data(
    init_data: str,
    bot_token: str,
    max_age_seconds: int = _AUTH_DATE_MAX_AGE,
) -> dict[str, Any]:
    """Verify ``Telegram.WebApp.initData`` of a Mini App and return its fields.

    Implements https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    — differs from the Login Widget check above: the HMAC secret is
    ``HMAC_SHA256(key="WebAppData", msg=bot_token)``, and the payload is a
    URL-encoded query string, not a JSON object.

    Returns:
        The decoded fields; ``user`` is parsed from JSON into a dict.

    Raises:
        TelegramHashError: missing/invalid hash, stale or future ``auth_date``,
            or no usable ``user.id``.
    """
    if not bot_token:
        raise TelegramHashError("Bot token is not configured")

    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        raise TelegramHashError("Malformed init data")
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise TelegramHashError("Duplicate init data fields")

    received_hash = fields.pop("hash", None)
    if not received_hash:
        raise TelegramHashError("Missing hash field")

    check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac_lib.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac_lib.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac_lib.compare_digest(expected, received_hash):
        raise TelegramHashError("Invalid Telegram hash")

    try:
        auth_date = int(fields.get("auth_date", ""))
    except ValueError:
        raise TelegramHashError("Missing auth_date")
    age = time.time() - auth_date
    if age > max_age_seconds:
        raise TelegramHashError("Auth data expired")
    if age < -_AUTH_DATE_FUTURE_SKEW:
        raise TelegramHashError("Auth date is in the future")

    try:
        user = json.loads(fields.get("user", ""))
    except ValueError:
        raise TelegramHashError("Missing user")
    user_id = user.get("id") if isinstance(user, dict) else None
    if not isinstance(user_id, int) or isinstance(user_id, bool):
        raise TelegramHashError("Missing user id")
    fields["user"] = user
    return fields
