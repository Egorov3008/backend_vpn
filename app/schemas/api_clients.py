from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class ApiClientCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    scopes: List[str] = Field(default_factory=list)


class ApiClientResponse(BaseModel):
    id: int
    name: str
    key_prefix: str
    scopes: List[str]
    is_active: bool
    created_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None

    @classmethod
    def from_client(cls, c) -> "ApiClientResponse":
        return cls(
            id=c.id,
            name=c.name,
            key_prefix=c.key_prefix,
            scopes=c.scopes,
            is_active=c.is_active,
            created_at=c.created_at,
            last_used_at=c.last_used_at,
            revoked_at=c.revoked_at,
        )


class ApiClientCreatedResponse(ApiClientResponse):
    """Возвращается один раз — при создании/ротации. Сырой ключ дальше
    нигде не хранится и не восстанавливается (см. ApiClient.key_hash)."""
    api_key: str


class ApiClientsListResponse(BaseModel):
    clients: List[ApiClientResponse]
