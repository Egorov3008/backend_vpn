from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional, Dict, Any


def default_created_at():
    return datetime.now(timezone.utc)


def default_updated_at():
    return datetime.now(timezone.utc)


@dataclass
class User:
    tg_id: int
    created_at: datetime = None
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    language_code: Optional[str] = None
    is_bot: bool = False
    updated_at: datetime = None
    is_admin: bool = False
    balance: float = 0.0
    trial: int = 0
    server_id: Optional[int] = None
    referral_id: Optional[int] = None
    check_referral: bool = False
    is_blocked: bool = False
    _name = "user"

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = default_created_at()
        if self.updated_at is None:
            self.updated_at = default_updated_at()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "User":
        """Создает экземпляр User из словаря."""
        return cls(**data)

    @property
    def name(self) -> str:
        return self._name
