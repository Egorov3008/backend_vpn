from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, Any, Optional, ClassVar


def default_created_at():
    return datetime.utcnow()


@dataclass
class ReferralLink:
    referrer_tg_id: int
    token: str
    created_at: Optional[datetime] = None
    id: Optional[int] = None  # SERIAL в БД, исключено из INSERT через _DB_FIELDS

    # Поля для сохранения в БД; id исключён (SERIAL/генерируется БД)
    _DB_FIELDS: ClassVar[frozenset] = frozenset(
        {"referrer_tg_id", "token", "created_at"}
    )

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = default_created_at()

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if k in self._DB_FIELDS}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReferralLink":
        return cls(**data)
