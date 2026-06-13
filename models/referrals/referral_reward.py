from dataclasses import dataclass, asdict
from datetime import datetime
from decimal import Decimal
from typing import Dict, Any, Optional, ClassVar, Union


def default_awarded_at():
    return datetime.utcnow()


@dataclass
class ReferralReward:
    referrer_tg_id: int
    reward_type: str
    # BUG-8: тип в БД изменён TEXT → DECIMAL(10,2). Храним как Decimal,
    # принимаем str/int/float для обратной совместимости с тестами/легаси.
    reward_value: Union[Decimal, str, int, float]
    awarded_at: Optional[datetime] = None
    id: Optional[int] = None  # SERIAL в БД, исключено из INSERT через _DB_FIELDS

    # Поля для сохранения в БД; id исключён (SERIAL/генерируется БД)
    _DB_FIELDS: ClassVar[frozenset] = frozenset(
        {"referrer_tg_id", "reward_type", "reward_value", "awarded_at"}
    )

    def __post_init__(self):
        if self.awarded_at is None:
            self.awarded_at = default_awarded_at()
        # Нормализуем к Decimal, чтобы избежать сюрпризов при сериализации.
        # БД ждёт DECIMAL(10,2); Decimal пробрасывается asyncpg как numeric.
        if not isinstance(self.reward_value, Decimal):
            self.reward_value = Decimal(str(self.reward_value))

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if k in self._DB_FIELDS}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReferralReward":
        return cls(**data)
