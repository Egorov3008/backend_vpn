"""
Service functions for authentication and user registration from invites.
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Awaitable, Callable

import asyncpg

from models import User, LoginCode
from app.schemas.auth import (
    RegisterFromInviteRequest,
    RegisterFromInviteResponse,
    TelegramAuthData,
)
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


async def telegram_login(
    data: TelegramAuthData,
    user_repo: UserRepository,
    notify_fn: Callable[[int], Awaitable[Any]],
) -> dict:
    """Handle a verified Telegram Login Widget payload.

    The HMAC signature on ``data`` must already have been validated by the
    caller via :func:`app.core.telegram.verify_telegram_hash`.

    Behaviour:
        - If a user with ``data.id`` does not exist, a new ``User`` row is
          created and ``notify_fn(tg_id)`` is awaited (e.g. send a Telegram
          welcome DM).
        - If the user already exists, no notification is sent.

    Args:
        data: Verified ``TelegramAuthData`` payload from the login widget.
        user_repo: Repository used to look up and create users.
        notify_fn: Async callable invoked with the new user's ``tg_id``
            when a fresh account is created.

    Returns:
        A dict with keys ``tg_id``, ``is_admin``, ``is_new`` suitable for
        constructing :class:`TelegramLoginResponse`.
    """
    tg_id = data.id
    existing_user = await user_repo.get_by_tg_id(tg_id)

    if existing_user is not None:
        logger.info(f"Telegram login for existing user: tg_id={tg_id}")
        is_admin = bool(getattr(existing_user, "is_admin", False))
        return {"tg_id": tg_id, "is_admin": is_admin, "is_new": False}

    new_user = User(
        tg_id=tg_id,
        username=data.username,
        first_name=data.first_name,
        last_name=data.last_name,
        language_code=None,
        server_id=None,
        balance=0.0,
        trial=0,
        is_admin=False,
        is_blocked=False,
    )

    created_user = await user_repo.create(new_user)
    if not created_user:
        # Repository may return ``None`` on insert failure; surface a clear error.
        logger.error(f"Failed to create user from Telegram login: tg_id={tg_id}")
        raise ValueError("Failed to create user from Telegram login")

    try:
        await notify_fn(tg_id)
    except Exception as exc:
        # Notification failures must not break the login flow.
        logger.warning(
            f"telegram_login notify_fn failed for tg_id={tg_id}: {exc}",
            exc_info=True,
        )

    is_admin = bool(getattr(created_user, "is_admin", False))
    logger.info(f"New Telegram user registered: tg_id={tg_id}")
    return {"tg_id": tg_id, "is_admin": is_admin, "is_new": True}
