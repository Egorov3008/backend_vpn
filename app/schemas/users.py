from pydantic import BaseModel
from typing import Optional


from datetime import datetime


class UserResponse(BaseModel):
    tg_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    balance: float = 0.0
    trial: int = 0
    server_id: Optional[int] = None
    is_admin: bool = False
    is_blocked: bool = False
    created_at: Optional[datetime] = None

    @classmethod
    def from_user(cls, u) -> "UserResponse":
        return cls(
            tg_id=u.tg_id,
            username=u.username,
            first_name=u.first_name,
            balance=u.balance,
            trial=u.trial or 0,
            server_id=u.server_id,
            is_admin=u.is_admin,
            is_blocked=u.is_blocked,
            created_at=getattr(u, "created_at", None),
        )


class UserRegisterRequest(BaseModel):
    tg_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    language_code: Optional[str] = None
    server_id: Optional[int] = None
    referral_id: Optional[int] = None
    referral_link_id: Optional[int] = None


class UserUpdateRequest(BaseModel):
    balance: Optional[float] = None
    server_id: Optional[int] = None
    trial: Optional[int] = None
    is_blocked: Optional[bool] = None
