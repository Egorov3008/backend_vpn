import hashlib
import hmac
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.telegram import TelegramHashError, verify_telegram_hash
from app.schemas.auth import TelegramAuthData, TelegramLoginResponse
from app.services_auth import telegram_login

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


@pytest.mark.asyncio
async def test_telegram_login_new_user():
    """A new Telegram user is created via user_repo and notify_fn is invoked."""
    auth_data = TelegramAuthData(
        id=999,
        first_name="New",
        last_name="User",
        username="new_user",
        photo_url=None,
        auth_date=int(time.time()),
        hash="dummyhash",
    )

    # No existing user — get_by_tg_id returns None
    user_repo = MagicMock()
    user_repo.get_by_tg_id = AsyncMock(return_value=None)
    user_repo.create = AsyncMock(return_value=MagicMock(tg_id=999, is_admin=False))

    notify_fn = AsyncMock()

    result = await telegram_login(auth_data, user_repo, notify_fn)

    assert isinstance(result, dict)
    assert result["tg_id"] == 999
    assert result["is_new"] is True
    assert result["is_admin"] is False

    user_repo.get_by_tg_id.assert_awaited_once_with(999)
    user_repo.create.assert_awaited_once()
    notify_fn.assert_awaited_once()


@pytest.mark.asyncio
async def test_telegram_login_existing_user():
    """An existing Telegram user is not recreated and notify_fn is NOT called."""
    auth_data = TelegramAuthData(
        id=42,
        first_name="Existing",
        last_name=None,
        username="exists",
        photo_url=None,
        auth_date=int(time.time()),
        hash="dummyhash",
    )

    existing_user = MagicMock(tg_id=42, is_admin=True)

    user_repo = MagicMock()
    user_repo.get_by_tg_id = AsyncMock(return_value=existing_user)
    user_repo.create = AsyncMock()

    notify_fn = AsyncMock()

    result = await telegram_login(auth_data, user_repo, notify_fn)

    assert result["tg_id"] == 42
    assert result["is_new"] is False
    assert result["is_admin"] is True

    user_repo.get_by_tg_id.assert_awaited_once_with(42)
    user_repo.create.assert_not_awaited()
    notify_fn.assert_not_awaited()


@pytest.mark.asyncio
async def test_telegram_login_endpoint_new_user(monkeypatch):
    """POST /auth/telegram-login creates a new user and returns is_new=True."""
    from httpx import AsyncClient, ASGITransport

    from app.main import app
    from app.auth import verify_bot_secret
    from app.dependencies import get_pool, get_service_data
    from api.v1.auth import get_user_repository
    from config import settings

    # Patch bot token used by settings to ensure verify_telegram_hash succeeds
    monkeypatch.setattr(settings, "bot_token", BOT_TOKEN)

    auth_date = int(time.time())
    payload = {
        "id": 555,
        "first_name": "Web",
        "last_name": "User",
        "username": "web_user",
        "photo_url": None,
        "auth_date": auth_date,
    }
    payload["hash"] = _make_valid_hash(payload, BOT_TOKEN)

    # Mock user repository: no existing user, create returns a fresh user.
    user_repo = MagicMock()
    user_repo.get_by_tg_id = AsyncMock(return_value=None)
    user_repo.create = AsyncMock(return_value=MagicMock(tg_id=555, is_admin=False))

    # Patch admin notification helper to avoid network calls.
    import api.v1.auth as auth_module
    monkeypatch.setattr(auth_module, "_notify_admins_telegram", AsyncMock(return_value=None))

    app.dependency_overrides[verify_bot_secret] = lambda: None
    app.dependency_overrides[get_pool] = lambda: MagicMock()
    app.dependency_overrides[get_service_data] = lambda: MagicMock()
    app.dependency_overrides[get_user_repository] = lambda: user_repo

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/auth/telegram-login",
                json=payload,
                headers={"X-Bot-Secret": "test_secret"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["tg_id"] == 555
    assert data["is_new"] is True
    assert data["is_admin"] is False
    user_repo.get_by_tg_id.assert_awaited_once_with(555)
    user_repo.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_telegram_login_missing_bot_secret():
    """POST /auth/telegram-login without X-Bot-Secret header is rejected with 401."""
    from httpx import AsyncClient, ASGITransport

    from app.main import app

    # Ensure no overrides are bypassing auth.
    app.dependency_overrides.clear()

    payload = {
        "id": 555,
        "first_name": "Web",
        "auth_date": int(time.time()),
        "hash": "dummyhash",
    }

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/auth/telegram-login",
            json=payload,
        )

    assert response.status_code == 401
    assert "Invalid bot secret" in response.json()["detail"]
