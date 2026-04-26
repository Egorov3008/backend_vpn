from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, Any


def default_created_at():
    return datetime.now()


@dataclass
class Referral:
    referral_id: int
    referrer_id: int
    token: str
    discount_percent: float
    max_usages: int
    current_usages: int
    created_at: datetime = None
    is_active: bool = True

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = default_created_at()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Referral":
        return cls(**data)
