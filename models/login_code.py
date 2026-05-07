from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional, Dict, Any


def default_created_at():
    return datetime.now(timezone.utc)


def default_expires_at():
    return datetime.now(timezone.utc)


@dataclass
class LoginCode:
    code: str
    tg_id: int
    expires_at: datetime
    id: Optional[int] = None
    used: bool = False
    created_at: datetime = None
    _name = "login_code"

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = default_created_at()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LoginCode":
        """Создает экземпляр LoginCode из словаря."""
        return cls(**data)

    @property
    def name(self) -> str:
        return self._name
