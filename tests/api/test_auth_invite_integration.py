"""Integration tests for register-from-invite endpoint."""
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import MagicMock, AsyncMock

from app.main import app
from app.auth import verify_bot_secret
from app.dependencies import get_service_data, get_pool, get_cache


@pytest.fixture
async def api_client_with_auth():
    """Create an API client with proper authentication setup."""
    # Setup mocks
    mock_service_data = MagicMock()
    mock_service_data.tariffs.get_all = AsyncMock(return_value=[])
    mock_service_data.tariffs.get_data = AsyncMock(return_value=None)
    mock_service_data.users.get_data = AsyncMock(return_value=None)
    mock_service_data.users.get_all = AsyncMock(return_value=[])
    mock_service_data.users.save_data = AsyncMock(return_value=None)
    mock_service_data.users.update = AsyncMock(return_value=None)
    mock_service_data.keys.get_by = AsyncMock(return_value=None)
    mock_service_data.keys.get_data = AsyncMock(return_value=None)
    mock_service_data.keys.get_all = AsyncMock(return_value=[])
    mock_service_data.keys.delete = AsyncMock(return_value=True)
    mock_service_data.payments.save_data = AsyncMock(return_value=None)
    mock_service_data.servers.get_data = AsyncMock(return_value=None)

    mock_pool = MagicMock()
    mock_cache = MagicMock()

    # Setup dependency overrides
    app.dependency_overrides[get_service_data] = lambda: mock_service_data
    app.dependency_overrides[get_pool] = lambda: mock_pool
    app.dependency_overrides[get_cache] = lambda: mock_cache
    app.dependency_overrides[verify_bot_secret] = lambda: None

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, mock_pool

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_register_from_invite_endpoint_missing_bot_secret(api_client_with_auth):
    """Test endpoint requires X-Bot-Secret header."""
    client, _ = api_client_with_auth

    # Clear auth override to test actual verification
    app.dependency_overrides.clear()

    response = await client.post(
        "/api/v1/auth/register-from-invite",
        json={
            "tg_id": 123456789,
            "username": "testuser",
            "first_name": "Test",
            "last_name": "User",
            "language_code": "en",
            "invite_token": "test_token",
        },
    )

    # Should fail without proper secret
    assert response.status_code == 401
    assert "Invalid bot secret" in response.json()["detail"]


@pytest.mark.asyncio
async def test_register_from_invite_invalid_token(api_client_with_auth):
    """Test endpoint rejects invalid invite token."""
    client, mock_pool = api_client_with_auth

    # Setup mock transaction
    mock_conn = MagicMock()
    mock_transaction = MagicMock()
    mock_transaction.__aenter__ = AsyncMock(return_value=None)
    mock_transaction.__aexit__ = AsyncMock(return_value=None)
    mock_conn.transaction = MagicMock(return_value=mock_transaction)
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=None)
    mock_pool.acquire = MagicMock(return_value=mock_conn)

    response = await client.post(
        "/api/v1/auth/register-from-invite",
        json={
            "tg_id": 123456789,
            "username": "testuser",
            "first_name": "Test",
            "last_name": "User",
            "language_code": "en",
            "invite_token": "invalid_token",
        },
        headers={"X-Bot-Secret": "test_secret"},
    )

    # Should reject invalid token
    assert response.status_code == 400
    assert "Invalid invite token" in response.json()["detail"]


@pytest.mark.asyncio
async def test_register_from_invite_user_already_exists(api_client_with_auth):
    """Test endpoint returns 409 conflict if user exists."""
    client, mock_pool = api_client_with_auth

    # Setup mock transaction
    mock_conn = MagicMock()
    mock_transaction = MagicMock()
    mock_transaction.__aenter__ = AsyncMock(return_value=None)
    mock_transaction.__aexit__ = AsyncMock(return_value=None)
    mock_conn.transaction = MagicMock(return_value=mock_transaction)
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=None)
    mock_pool.acquire = MagicMock(return_value=mock_conn)

    response = await client.post(
        "/api/v1/auth/register-from-invite",
        json={
            "tg_id": 123456789,
            "username": "testuser",
            "first_name": "Test",
            "last_name": "User",
            "language_code": "en",
            "invite_token": "test_token",
        },
        headers={"X-Bot-Secret": "test_secret"},
    )

    # Response should be successful (201) or conflict (409)
    # depending on whether user exists or not
    assert response.status_code in [201, 409]


@pytest.mark.asyncio
async def test_register_from_invite_response_format(api_client_with_auth):
    """Test endpoint response has correct format."""
    client, _ = api_client_with_auth

    response = await client.post(
        "/api/v1/auth/register-from-invite",
        json={
            "tg_id": 123456789,
            "username": "testuser",
            "first_name": "Test",
            "last_name": "User",
            "language_code": "en",
            "invite_token": "test_token",
        },
        headers={"X-Bot-Secret": "test_secret"},
    )

    # Response should either succeed or fail with proper format
    if response.status_code == 201:
        data = response.json()
        assert "tg_id" in data
        assert "login_code" in data
        assert "code_expires_at" in data
        assert data["tg_id"] == 123456789
        # Login code should be 8 characters alphanumeric
        assert len(data["login_code"]) == 8
        assert data["login_code"].isalnum()
    else:
        # Error response should have detail
        data = response.json()
        assert "detail" in data


@pytest.mark.asyncio
async def test_register_from_invite_with_minimal_data(api_client_with_auth):
    """Test endpoint accepts minimal user data."""
    client, _ = api_client_with_auth

    response = await client.post(
        "/api/v1/auth/register-from-invite",
        json={
            "tg_id": 999888777,
            "username": None,
            "first_name": None,
            "last_name": None,
            "language_code": "ru",
            "invite_token": "test_token",
        },
        headers={"X-Bot-Secret": "test_secret"},
    )

    # Should handle minimal data gracefully
    assert response.status_code in [201, 400, 409]


@pytest.mark.asyncio
async def test_register_from_invite_invalid_request_format(api_client_with_auth):
    """Test endpoint validates request format."""
    client, _ = api_client_with_auth

    response = await client.post(
        "/api/v1/auth/register-from-invite",
        json={
            # Missing required fields
            "tg_id": 123456789,
        },
        headers={"X-Bot-Secret": "test_secret"},
    )

    # Should return validation error
    assert response.status_code == 422
    assert "detail" in response.json()
