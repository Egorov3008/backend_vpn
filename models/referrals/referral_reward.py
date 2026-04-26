from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, Any, Optional, ClassVar


def default_awarded_at():
    return datetime.utcnow()


@dataclass
class ReferralReward:
    referrer_tg_id: int
    reward_type: str
    reward_value: str
    awarded_at: Optional[datetime] = None
    is_claimed: bool = False
    id: Optional[int] = None  # SERIAL в БД, исключено из INSERT через _DB_FIELDS

    # Поля для сохранения в БД; id исключён (SERIAL/генерируется БД)
    _DB_FIELDS: ClassVar[frozenset] = frozenset(
        {"referrer_tg_id", "reward_type", "reward_value", "awarded_at", "is_claimed"}
    )

    def __post_init__(self):
        if self.awarded_at is None:
            self.awarded_at = default_awarded_at()

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if k in self._DB_FIELDS}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReferralReward":
        return cls(**data)
