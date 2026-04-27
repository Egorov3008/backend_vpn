"""Unit tests for invite registration functionality."""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.schemas.auth import RegisterFromInviteRequest, RegisterFromInviteResponse
from app.services_auth import register_from_invite
from models import User, LoginCode


@pytest.fixture
def valid_request():
    """Create a valid RegisterFromInviteRequest."""
    return RegisterFromInviteRequest(
        tg_id=12345678,
        username="testuser",
        first_name="Test",
        last_name="User",
        language_code="en",
        invite_token="test_invite_token"
    )


@pytest.fixture
def mock_user_repo():
    """Create a mock UserRepository."""
    repo = MagicMock()
    repo.get_by_tg_id = AsyncMock(return_value=None)
    repo.create = AsyncMock()
    return repo


@pytest.fixture
def mock_login_code_repo():
    """Create a mock LoginCodeRepository."""
    repo = MagicMock()
    repo.create = AsyncMock()
    return repo


@pytest.fixture
def mock_pool():
    """Create a mock asyncpg pool with transaction context."""
    pool = MagicMock()
    conn = MagicMock()
    transaction = MagicMock()

    # Setup transaction context manager
    transaction.__aenter__ = AsyncMock(return_value=None)
    transaction.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=transaction)

    # Setup connection context manager
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=conn)

    return pool


@pytest.mark.asyncio
async def test_register_from_invite_success(
    valid_request, mock_user_repo, mock_login_code_repo, mock_pool
):
    """Test successful user registration from invite."""
    # Setup mock returns
    created_user = User(
        tg_id=valid_request.tg_id,
        username=valid_request.username,
        first_name=valid_request.first_name,
        last_name=valid_request.last_name,
        language_code=valid_request.language_code,
        server_id=None,
        balance=0.0,
        trial=0,
        is_admin=False,
        is_blocked=False
    )
    mock_user_repo.create.return_value = created_user

    created_code = LoginCode(
        code="ABC12345",
        tg_id=valid_request.tg_id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24)
    )
    mock_login_code_repo.create.return_value = created_code

    with patch("app.services_auth.settings") as mock_settings:
        mock_settings.invite_token = valid_request.invite_token

        result = await register_from_invite(
            valid_request, mock_user_repo, mock_login_code_repo, mock_pool
        )

    # Verify result
    assert isinstance(result, RegisterFromInviteResponse)
    assert result.tg_id == valid_request.tg_id
    assert result.login_code == "ABC12345"
    assert result.code_expires_at is not None

    # Verify mocks were called correctly
    mock_user_repo.get_by_tg_id.assert_called_once_with(valid_request.tg_id)
    mock_user_repo.create.assert_called_once()
    mock_login_code_repo.create.assert_called_once()


@pytest.mark.asyncio
async def test_register_from_invite_invalid_token(
    valid_request, mock_user_repo, mock_login_code_repo, mock_pool
):
    """Test registration fails with invalid invite token."""
    with patch("app.services_auth.settings") as mock_settings:
        mock_settings.invite_token = "different_token"

        with pytest.raises(ValueError, match="Invalid invite token"):
            await register_from_invite(
                valid_request, mock_user_repo, mock_login_code_repo, mock_pool
            )

    # Verify no database operations occurred
    mock_user_repo.get_by_tg_id.assert_not_called()
    mock_user_repo.create.assert_not_called()
    mock_login_code_repo.create.assert_not_called()


@pytest.mark.asyncio
async def test_register_from_invite_user_already_exists(
    valid_request, mock_user_repo, mock_login_code_repo, mock_pool
):
    """Test registration fails if user already exists."""
    # Setup mock to return existing user
    existing_user = User(
        tg_id=valid_request.tg_id,
        username="existing",
        first_name="Existing",
        last_name="User",
        language_code="en",
        server_id=None,
        balance=0.0,
        trial=0,
        is_admin=False,
        is_blocked=False
    )
    mock_user_repo.get_by_tg_id.return_value = existing_user

    with patch("app.services_auth.settings") as mock_settings:
        mock_settings.invite_token = valid_request.invite_token

        with pytest.raises(ValueError, match="already exists"):
            await register_from_invite(
                valid_request, mock_user_repo, mock_login_code_repo, mock_pool
            )

    # Verify no creation occurred
    mock_user_repo.create.assert_not_called()
    mock_login_code_repo.create.assert_not_called()


@pytest.mark.asyncio
async def test_register_from_invite_user_creation_fails(
    valid_request, mock_user_repo, mock_login_code_repo, mock_pool
):
    """Test registration fails if user creation fails."""
    mock_user_repo.get_by_tg_id.return_value = None
    mock_user_repo.create.return_value = None  # Simulate creation failure

    with patch("app.services_auth.settings") as mock_settings:
        mock_settings.invite_token = valid_request.invite_token

        with pytest.raises(ValueError, match="Failed to create user"):
            await register_from_invite(
                valid_request, mock_user_repo, mock_login_code_repo, mock_pool
            )

    # Verify no code creation attempted
    mock_login_code_repo.create.assert_not_called()


@pytest.mark.asyncio
async def test_register_from_invite_code_creation_fails(
    valid_request, mock_user_repo, mock_login_code_repo, mock_pool
):
    """Test registration fails if login code creation fails."""
    # User is created successfully
    created_user = User(
        tg_id=valid_request.tg_id,
        username=valid_request.username,
        first_name=valid_request.first_name,
        last_name=valid_request.last_name,
        language_code=valid_request.language_code,
        server_id=None,
        balance=0.0,
        trial=0,
        is_admin=False,
        is_blocked=False
    )
    mock_user_repo.create.return_value = created_user

    # Code creation fails
    mock_login_code_repo.create.return_value = None

    with patch("app.services_auth.settings") as mock_settings:
        mock_settings.invite_token = valid_request.invite_token

        with pytest.raises(ValueError, match="Failed to create login code"):
            await register_from_invite(
                valid_request, mock_user_repo, mock_login_code_repo, mock_pool
            )


@pytest.mark.asyncio
async def test_register_from_invite_with_minimal_data(
    mock_user_repo, mock_login_code_repo, mock_pool
):
    """Test registration with minimal user data (username, names optional)."""
    request = RegisterFromInviteRequest(
        tg_id=99999999,
        username=None,
        first_name=None,
        last_name=None,
        language_code="en",
        invite_token="test_invite_token"
    )

    created_user = User(
        tg_id=request.tg_id,
        username=None,
        first_name=None,
        last_name=None,
        language_code=request.language_code,
        server_id=None,
        balance=0.0,
        trial=0,
        is_admin=False,
        is_blocked=False
    )
    mock_user_repo.create.return_value = created_user

    created_code = LoginCode(
        code="XYZ98765",
        tg_id=request.tg_id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24)
    )
    mock_login_code_repo.create.return_value = created_code

    with patch("app.services_auth.settings") as mock_settings:
        mock_settings.invite_token = request.invite_token

        result = await register_from_invite(
            request, mock_user_repo, mock_login_code_repo, mock_pool
        )

    assert result.tg_id == request.tg_id
    assert result.login_code == "XYZ98765"
