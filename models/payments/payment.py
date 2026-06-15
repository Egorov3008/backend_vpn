from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Optional, Dict, Any, ClassVar


@dataclass
class PaymentModel:
    payment_id: str
    tg_id: Optional[int] = None
    amount: Optional[float] = None
    payment_type: Optional[str] = None
    status: str = "pending"
    number_of_months: int = 1
    discount_percent: int = 0
    referral_discount: float = 0.0
    balance_discount: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # id — SERIAL в БД, генерируется автоматически; хранится в экземпляре при чтении,
    # но исключается из INSERT через _DB_FIELDS whitelist (как в Key._DB_FIELDS)
    id: Optional[int] = None
    _name: ClassVar[str] = "payment"

    # Поля, реально существующие в таблице payments; id исключён — он SERIAL/генерируется БД
    _DB_FIELDS: ClassVar[frozenset] = frozenset(
        {"payment_id", "tg_id", "amount", "payment_type", "status", "created_at", "number_of_months", "discount_percent", "referral_discount", "balance_discount"}
    )

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now(timezone.utc)

    def to_dict(self) -> Dict[str, Any]:
        # Возвращаем только поля БД (id не включаем — он SERIAL, БД генерирует сама)
        return {k: v for k, v in asdict(self).items() if k in self._DB_FIELDS}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PaymentModel":
        return cls(**data)

    @property
    def name(self) -> str:
        return self._name
