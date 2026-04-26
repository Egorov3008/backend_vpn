from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import Dict, Any, Optional, ClassVar


@dataclass
class GiftLink:
    sender_tg_id: int
    tariff_id: int
    token: str
    id: Optional[int] = None
    created_at: datetime = field(default_factory=datetime.now)
    recipient_tg_id: Optional[int] = None
    email: Optional[str] = None
    used_at: Optional[datetime] = None
    _status: str = "active"  # instance field, but excluded from DB via _DB_FIELDS
    _name: ClassVar[str] = "gift_links"

    # Поля для сохранения в БД; id и _status исключены (id=SERIAL, _status только для логики)
    _DB_FIELDS: ClassVar[frozenset] = frozenset(
        {
            "sender_tg_id",
            "tariff_id",
            "token",
            "created_at",
            "recipient_tg_id",
            "email",
            "used_at",
        }
    )

    def __post_init__(self):
        """Инициализация после создания объекта"""
        if self.recipient_tg_id and not self.used_at:
            self.used_at = datetime.now()
        if self.recipient_tg_id:
            self._status = "redeemed"

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if k in self._DB_FIELDS}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GiftLink":
        return cls(**data)

    def is_redeemable(self) -> bool:
        """Проверяет, можно ли активировать подарок"""
        return self._status == "active"

    def redeem(self, recipient_tg_id: int, email: str) -> None:
        """Активирует подарок"""
        if self.sender_tg_id == recipient_tg_id:
            raise ValueError("Нельзя активировать подарок самому себе")
        if not self.is_redeemable():
            raise ValueError(
                f"Подарок недоступен для активации (статус: {self._status})"
            )

        self.recipient_tg_id = recipient_tg_id
        self.email = email
        self.used_at = datetime.now()
        self._status = "redeemed"

    def is_expired(self, max_days: int = 30) -> bool:
        """Проверяет, истек ли срок действия подарка"""
        return (datetime.now() - self.created_at).days > max_days

    @property
    def name(self):
        return self._name

    @property
    def status(self):
        return self._status
