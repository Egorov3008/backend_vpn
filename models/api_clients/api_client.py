from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class ApiClient:
    """Внешний потребитель публичного REST API (Этап 4 — не bot/web/mobile-mvp,
    у которых свои статичные shared-секреты, см. app/auth.py).

    Намеренно НЕ интегрирован в CacheService/CacheKeyManager: это
    admin-управляемые, редко меняющиеся auth-данные с низким объёмом
    (в отличие от users/keys/tariffs), поэтому здесь важнее корректность
    (мгновенный revoke) чем задержка одного SELECT на запрос.
    """

    id: int
    name: str
    key_prefix: str
    key_hash: str
    scopes: List[str]
    is_active: bool = True
    created_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ApiClient":
        return cls(**data)
