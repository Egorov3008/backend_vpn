from dataclasses import dataclass
from typing import Tuple

from config import DISCOUNTS
from models.price_model.price import Number


@dataclass(frozen=True)
class PriceResult:
    """Результат расчёта цены тарифа со скидкой."""

    original_amount: Number  # базовая цена тарифа
    final_amount: Number  # цена со скидкой
    stock_value: Number  # значение скидки (0 если нет)
    stock_type: str  # "fix"/"percent"/""
    has_discount: bool  # есть ли активная скидка

    def total(self, months: int = 1) -> float:
        """Итоговая сумма за N месяцев."""
        return float(self.final_amount) * months


def apply_volume_discount(
    amount_per_month: float, months: int
) -> Tuple[float, float, int]:
    """
    Применяет скидку за объём (3% для 2-6 месяцев).

    Возвращает (итого_со_скидкой, итого_без_скидки, процент_скидки).
    """
    percent = DISCOUNTS if 2 <= months <= 6 else 0
    total_before = amount_per_month * months
    total_after = (
        round(total_before * (1 - percent / 100), 2) if percent else total_before
    )
    return total_after, total_before, percent
