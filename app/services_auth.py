"""
Service functions for authentication and user registration from invites.
"""
import logging
from datetime import datetime, timezone, timedelta

import asyncpg

from models import User, LoginCode
from app.schemas.auth import RegisterFromInviteRequest, RegisterFromInviteResponse
from app.repositories.users import UserRepository
from app.repositories.login_codes import LoginCodeRepository
from core.utils import generate_login_code
from config import settings

logger = logging.getLogger(__name__)


async def register_from_invite(
    request: RegisterFromInviteRequest,
    user_repo: UserRepository,
    login_code_repo: LoginCodeRepository,
    pool: asyncpg.Pool
) -> RegisterFromInviteResponse:
    """
    Register new user from web invite and generate login code.

    Performs user creation and login code generation within a single transaction
    to ensure consistency. If either operation fails, the entire transaction is
    rolled back.

    Args:
        request: RegisterFromInviteRequest with user data and invite token
        user_repo: UserRepository for user operations
        login_code_repo: LoginCodeRepository for login code operations
        pool: asyncpg pool for database operations

    Returns:
        RegisterFromInviteResponse with generated login code and expiry

    Raises:
        ValueError: If invite token is invalid or user already exists
        asyncpg.PostgresError: If database operation fails
    """

    # Validate invite token
    if request.invite_token != settings.invite_token:
        logger.warning(f"Invalid invite token attempt: {request.invite_token}")
        raise ValueError("Invalid invite token")

    # Check if user already exists
    existing_user = await user_repo.get_by_tg_id(request.tg_id)
    if existing_user:
        logger.warning(f"Registration attempt for existing user: tg_id={request.tg_id}")
        raise ValueError(f"User {request.tg_id} already exists")

    # Create user model
    new_user = User(
        tg_id=request.tg_id,
        username=request.username,
        first_name=request.first_name,
        last_name=request.last_name,
        language_code=request.language_code,
        server_id=None,
        balance=0.0,
        trial=0,
        is_admin=False,
        is_blocked=False
    )

    # Start transaction for both user and code creation
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Create user in transaction
            created_user = await user_repo.create(new_user, conn=conn)
            if not created_user:
                logger.error(f"Failed to create user: tg_id={request.tg_id}")
                raise ValueError("Failed to create user")

            # Generate login code
            code = generate_login_code()
            expires_at = datetime.now(timezone.utc) + timedelta(hours=24)

            # Save code in transaction
            saved_code = await login_code_repo.create(
                code=code,
                tg_id=created_user.tg_id,
                expires_at=expires_at,
                conn=conn
            )
            if not saved_code:
                logger.error(f"Failed to create login code for user: tg_id={request.tg_id}")
                raise ValueError("Failed to create login code")

            logger.info(
                f"User registered from invite: tg_id={request.tg_id}, "
                f"code_expires_in_hours=24"
            )

            return RegisterFromInviteResponse(
                tg_id=created_user.tg_id,
                login_code=code,
                code_expires_at=expires_at
            )
