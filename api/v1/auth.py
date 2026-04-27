"""Authentication and registration endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.auth import verify_bot_secret
from app.dependencies import get_pool
from app.repositories.users import UserRepository
from app.repositories.login_codes import LoginCodeRepository
from app.schemas.auth import RegisterFromInviteRequest, RegisterFromInviteResponse
from app.services_auth import register_from_invite

router = APIRouter(prefix="/auth", tags=["auth"])


def get_user_repository(pool=Depends(get_pool)) -> UserRepository:
    """Dependency to provide UserRepository."""
    from services.core.data.service import ServiceDataModel
    from app.dependencies import get_service_data

    # This will be resolved through FastAPI's dependency system
    return UserRepository(pool=pool)


def get_login_code_repository(pool=Depends(get_pool)) -> LoginCodeRepository:
    """Dependency to provide LoginCodeRepository."""
    from services.core.data.service import ServiceDataModel
    from app.dependencies import get_service_data

    # This will be resolved through FastAPI's dependency system
    return LoginCodeRepository(pool=pool)


@router.post("/register-from-invite", response_model=RegisterFromInviteResponse, status_code=201)
async def register_from_invite_endpoint(
    request: RegisterFromInviteRequest,
    _: None = Depends(verify_bot_secret),
    pool=Depends(get_pool),
    user_repo: UserRepository = Depends(get_user_repository),
    login_code_repo: LoginCodeRepository = Depends(get_login_code_repository),
):
    """Register new user from web invite (bot only).

    This endpoint allows the bot to register new users with an invite token.
    Requires X-Bot-Secret header for authentication.

    Args:
        request: RegisterFromInviteRequest containing user data and invite token
        pool: asyncpg pool for database operations
        user_repo: UserRepository for user operations
        login_code_repo: LoginCodeRepository for login code operations

    Returns:
        RegisterFromInviteResponse with generated login code

    Raises:
        HTTPException: If invite token is invalid, user already exists, or database operation fails
    """
    try:
        result = await register_from_invite(request, user_repo, login_code_repo, pool)
        return result
    except ValueError as e:
        error_msg = str(e)
        if "already exists" in error_msg:
            raise HTTPException(status_code=409, detail=error_msg)
        raise HTTPException(status_code=400, detail=error_msg)
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error during registration")
