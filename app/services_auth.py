"""
Service functions for authentication and user registration from invites.
"""
from datetime import datetime, timedelta

from models import User, LoginCode
from app.schemas.auth import RegisterFromInviteRequest, RegisterFromInviteResponse
from core.utils import generate_login_code
from config import settings
from services.core.data.service import ServiceDataModel
import asyncpg


async def register_from_invite(
    request: RegisterFromInviteRequest,
    service_data: ServiceDataModel,
    pool: asyncpg.Pool
) -> RegisterFromInviteResponse:
    """
    Register new user from web invite and generate login code.

    Args:
        request: RegisterFromInviteRequest with user data and invite token
        service_data: ServiceDataModel with database repositories
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
    existing_user = await service_data.users.get_data(request.tg_id)
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
    await service_data.users.save_data(pool, new_user, tg_id=new_user.tg_id)

    # Generate login code
    code = generate_login_code()
    expires_at = datetime.utcnow() + timedelta(hours=24)

    # Save login code directly to database (bypasses cache as codes are temporary)
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
