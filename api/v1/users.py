import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.auth import verify_bot_secret
from app.dependencies import get_service_data, get_pool
from app.schemas.users import UserResponse, UserRegisterRequest, UserUpdateRequest
from models import User
from services.core.data.service import ServiceDataModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/users", tags=["users"])


@router.get("/{tg_id}", response_model=UserResponse)
async def get_user(
    tg_id: int,
    _: None = Depends(verify_bot_secret),
    service_data: ServiceDataModel = Depends(get_service_data),
) -> UserResponse:
    try:
        user = await service_data.users.get_data(tg_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return UserResponse.from_user(user)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user tg_id={tg_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/register")
async def register_user(
    body: UserRegisterRequest,
    _: None = Depends(verify_bot_secret),
    service_data: ServiceDataModel = Depends(get_service_data),
    pool=Depends(get_pool),
):
    existing = await service_data.users.get_data(body.tg_id)
    if existing:
        return UserResponse.from_user(existing)

    user = User(
        tg_id=body.tg_id,
        username=body.username,
        first_name=body.first_name,
        last_name=body.last_name,
        language_code=body.language_code,
        server_id=body.server_id,
    )
    await service_data.users.save_data(pool, user, tg_id=user.tg_id)
    return JSONResponse(status_code=201, content=UserResponse.from_user(user).model_dump(mode="json"))


@router.patch("/{tg_id}", response_model=UserResponse)
async def update_user(
    tg_id: int,
    body: UserUpdateRequest,
    _: None = Depends(verify_bot_secret),
    service_data: ServiceDataModel = Depends(get_service_data),
    pool=Depends(get_pool),
) -> UserResponse:
    user = await service_data.users.get_data(tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if body.balance is not None:
        user.balance = body.balance
    if body.server_id is not None:
        user.server_id = body.server_id
    if body.trial is not None:
        user.trial = body.trial
    if body.is_blocked is not None:
        user.is_blocked = body.is_blocked

    await service_data.users.update(pool, user, search_data={"tg_id": tg_id})
    return UserResponse.from_user(user)
