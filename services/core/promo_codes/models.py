"""Данные промокодов."""
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

Audience = Literal["new", "all"]
PromoOperations = Literal["create_key", "renew_key", "any"]
Operation = Literal["create_key", "renew_key"]


@dataclass(frozen=True)
class PromoCode:
    id: int
    code: str
    discount_percent: int
    starts_at: datetime
    ends_at: datetime
    max_redemptions: Optional[int]
    audience: Audience
    operations: PromoOperations
    max_months: Optional[int]
    owner_tg_id: Optional[int]
    is_active: bool
    created_by: Optional[int] = None
    created_at: Optional[datetime] = None

    def discount_for(self, price_per_month: float, months: int) -> float:
        """Скидка в рублях: процент от помесячной цены за первые ``max_months`` месяцев."""
        discounted_months = min(months, self.max_months) if self.max_months else months
        return round(float(price_per_month) * discounted_months * self.discount_percent / 100, 2)


@dataclass(frozen=True)
class PromoRequest:
    """Кто и на что применяет код."""
    tg_id: int
    operation: Operation
    number_of_months: int


@dataclass(frozen=True)
class PromoCheck:
    """Итог проверки: ``promo`` — код подходит; иначе ``reason`` — почему нет."""
    promo: Optional[PromoCode] = None
    reason: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.promo is not None


@dataclass(frozen=True)
class PromoUsage:
    """Счётчики активаций кода (для списка в админке)."""
    used: int
    used_new_users: int
    reserved: int


@dataclass(frozen=True)
class PromoStats:
    """Итоги акции по коду."""
    used: int
    used_new_users: int
    used_existing_users: int
    reserved_now: int
    abandoned: int
    revenue: float
    discount_total: float
    payments_before: int
    payments_during: int
    payments_after: int
    repeat_payers: int
    window_days: int
