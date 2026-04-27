"""
Service functions for authentication and user registration from invites.
"""
from datetime import datetime, timedelta

import asyncpg

from models import User, LoginCode
from app.schemas.auth import RegisterFromInviteRequest, RegisterFromInviteResponse
from app.repositories.users import UserRepository
from app.repositories.login_codes import LoginCodeRepository
from core.utils import generate_login_code
from config import settings


async def register_from_invite(
    request: RegisterFromInviteRequest,
    user_repo: UserRepository,
    login_code_repo: LoginCodeRepository,
    pool: asyncpg.Pool
) -> RegisterFromInviteResponse:
    """
    Register new user from web invite and generate login code.

    Args:
        request: RegisterFromInviteRequest with user data and invite token
        user_repo: UserRepository for user operations
        login_code_repo: LoginCodeRepository for login code operations
        pool: asyncpg pool for database operations

    Returns:
        RegisterFromInviteResponse with generated login code and expiry

    Raises:
        ValueError: If invite token is invalid or user already exists
    """

    # Validate invite token
    if request.invite_token != settings.invite_token:
        raise ValueError("Invalid invite token")

    # Check if user already exists
    existing_user = await user_repo.get_by_tg_id(request.tg_id)
    if existing_user:
        raise ValueError(f"User {request.tg_id} already exists")

    # Create user
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
    # Save user using pool connection
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (tg_id, username, first_name, last_name, language_code, server_id, balance, trial, is_admin, is_blocked)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
            new_user.tg_id,
            new_user.username,
            new_user.first_name,
            new_user.last_name,
            new_user.language_code,
            new_user.server_id,
            new_user.balance,
            new_user.trial,
            new_user.is_admin,
            new_user.is_blocked
        )

    # Generate login code
    code = generate_login_code()
    expires_at = datetime.utcnow() + timedelta(hours=24)

    # Save login code directly to database
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO login_codes (code, tg_id, expires_at)
            VALUES ($1, $2, $3)
            """,
            code,
            request.tg_id,
            expires_at
        )

    return RegisterFromInviteResponse(
        tg_id=new_user.tg_id,
        login_code=code,
        code_expires_at=expires_at
    )
