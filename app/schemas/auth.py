from pydantic import BaseModel, Field, ConfigDict
from typing import Optional
from datetime import datetime


class RegisterFromInviteRequest(BaseModel):
    """Request to register a new user from web invite"""
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "tg_id": 123456789,
            "username": "john_doe",
            "first_name": "John",
            "last_name": "Doe",
            "language_code": "en",
            "invite_token": "web_invite_2026"
        }
    })

    tg_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    language_code: str = "en"
    invite_token: str


class RegisterFromInviteResponse(BaseModel):
    """Response with generated login code"""
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "tg_id": 123456789,
            "login_code": "ABC12345",
            "code_expires_at": "2026-04-28T12:34:56Z"
        }
    })

    tg_id: int
    login_code: str = Field(..., pattern=r'^[A-Z0-9]{8}$', description="8-character alphanumeric code")
    code_expires_at: datetime
