from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, Any, Optional, ClassVar


def default_redeemed_at():
    return datetime.utcnow()


@dataclass
class ReferralRedemption:
    referral_link_id: int
    referred_tg_id: int
    redeemed_at: Optional[datetime] = None
    id: Optional[int] = None  # SERIAL в БД, исключено из INSERT через _DB_FIELDS

    # Поля для сохранения в БД; id исключён (SERIAL/генерируется БД)
    _DB_FIELDS: ClassVar[frozenset] = frozenset(
        {"referral_link_id", "referred_tg_id", "redeemed_at"}
    )

    def __post_init__(self):
        if self.redeemed_at is None:
            self.redeemed_at = default_redeemed_at()

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if k in self._DB_FIELDS}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReferralRedemption":
        return cls(**data)
