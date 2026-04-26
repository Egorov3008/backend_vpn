from typing import List

from fastapi import APIRouter, Depends, HTTPException

from app.auth import verify_api_key
from app.dependencies import get_service_data, get_pool
from app.schemas.users import UserResponse, UserUpdateRequest
from services.core.data.service import ServiceDataModel
from services.core.keys.admin_report import KeyAdminReport

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(verify_api_key)],
)


@router.get("/stats")
async def get_stats(
    service_data: ServiceDataModel = Depends(get_service_data),
):
    keys = await service_data.keys.get_all()
    users = await service_data.users.get_all()
    stats = await KeyAdminReport().get_summary_stats(keys)
    return {"total_users": len(users), **stats}


@router.get("/users", response_model=List[UserResponse])
async def list_users(
    service_data: ServiceDataModel = Depends(get_service_data),
) -> List[UserResponse]:
    users = await service_data.users.get_all()
    return [UserResponse.from_user(u) for u in users]


@router.patch("/users/{tg_id}", response_model=UserResponse)
async def admin_update_user(
    tg_id: int,
    body: UserUpdateRequest,
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
