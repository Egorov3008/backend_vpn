from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional, Union, ClassVar

Number = Union[float, Decimal]


@dataclass
class Stock:
    """
    Модель скидки пользователя с поддержкой срока действия.
    """

    tg_id: int
    stock_type: str  # "fix" or "percent"
    value: Number = Decimal("0.0")
    is_active: bool = True
    valid_until: Optional[datetime] = None  # Скидка действует до этой даты
    created_at: datetime = None
    _name: ClassVar[Optional[str]] = "stock"

    def __post_init__(self):
        if self.value < 0:
            raise ValueError("value не может быть отрицательным")
        if self.stock_type not in ("fix", "percent"):
            raise ValueError("stock_type должен быть 'fix' или 'percent'")
        if self.created_at is None:
            self.created_at = datetime.utcnow()

    @property
    def is_valid(self) -> bool:
        """
        Проверяет, действует ли скидка прямо сейчас.
        Учитывает is_active и срок действия.
        """
        if not self.is_active:
            return False
        if self.valid_until is None:
            return True
        return datetime.utcnow() <= self.valid_until

    def __repr__(self) -> str:
        return (
            f"Stock(tg_id={self.tg_id}, type={self.stock_type}, "
            f"value={self.value}, active={self.is_active}, "
            f"valid_until={self.valid_until})"
        )

    @property
    def name(self):
        return self._name
