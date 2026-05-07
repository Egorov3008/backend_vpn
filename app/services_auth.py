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

    # Generate code params
    code = generate_login_code()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=24)

    # Check if user already exists — if so, just issue a login code
    existing_user = await user_repo.get_by_tg_id(request.tg_id)
    if existing_user:
        logger.info(f"Existing user requesting web login code: tg_id={request.tg_id}")
        try:
            async with pool.acquire() as conn:
                saved_code = await login_code_repo.create(
                    code=code,
                    tg_id=request.tg_id,
                    expires_at=expires_at,
                    conn=conn,
                )
                if not saved_code:
                    raise ValueError("Failed to create login code")
        except Exception as e:
            logger.error(f"Failed to create login code for existing user: tg_id={request.tg_id}, error={e}", exc_info=True)
            raise

        try:
            return RegisterFromInviteResponse(
                tg_id=request.tg_id,
                login_code=code,
                code_expires_at=expires_at,
            )
        except Exception as e:
            logger.error(f"Failed to create RegisterFromInviteResponse: {e}", exc_info=True)
            raise

    # New user — create account and issue code in one transaction
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
        is_blocked=False,
    )

    async with pool.acquire() as conn:
        async with conn.transaction():
            created_user = await user_repo.create(new_user, conn=conn)
            if not created_user:
                raise ValueError("Failed to create user")

            saved_code = await login_code_repo.create(
                code=code,
                tg_id=created_user.tg_id,
                expires_at=expires_at,
                conn=conn,
            )
            if not saved_code:
                raise ValueError("Failed to create login code")

    logger.info(f"New user registered from invite: tg_id={request.tg_id}")
    return RegisterFromInviteResponse(
        tg_id=request.tg_id,
        login_code=code,
        code_expires_at=expires_at,
    )
