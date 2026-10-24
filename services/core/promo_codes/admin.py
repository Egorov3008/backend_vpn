"""Управление промокодами из админки: создание, выключение, итоги."""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from services.core.promo_codes.models import Audience, PromoCode, PromoOperations, PromoStats
from services.core.promo_codes.repository import PromoCodeRepository
from services.core.promo_codes.service import CODE_PATTERN, normalize_code


class InvalidPromoCode(ValueError):
    """Параметры кода не проходят проверку; текст — для админа."""


@dataclass(frozen=True)
class NewPromoCode:
    code: str
    discount_percent: int
    starts_at: datetime
    ends_at: datetime
    max_redemptions: Optional[int] = None
    audience: Audience = "new"
    operations: PromoOperations = "any"
    max_months: Optional[int] = 1
    owner_tg_id: Optional[int] = None


class PromoCodeAdmin:
    def __init__(self, repository: PromoCodeRepository, max_percent: int):
        self._repo = repository
        self._max_percent = max_percent

    async def create(self, new: NewPromoCode, created_by: Optional[int]) -> PromoCode:
        code = normalize_code(new.code)
        if not CODE_PATTERN.match(code):
            raise InvalidPromoCode("Код: 4–32 символа, латиница, цифры, «-» и «_»")
        if not 1 <= new.discount_percent <= self._max_percent:
            raise InvalidPromoCode(f"Скидка должна быть от 1 до {self._max_percent}%")
        if new.starts_at.tzinfo is None or new.ends_at.tzinfo is None:
            raise InvalidPromoCode("Укажите даты с часовым поясом")
        if new.ends_at <= new.starts_at:
            raise InvalidPromoCode("Конец действия должен быть позже начала")
        return await self._repo.create(
            code=code,
            discount_percent=new.discount_percent,
            starts_at=new.starts_at,
            ends_at=new.ends_at,
            max_redemptions=new.max_redemptions,
            audience=new.audience,
            operations=new.operations,
            max_months=new.max_months,
            owner_tg_id=new.owner_tg_id,
            created_by=created_by,
        )

    async def set_active(self, promo_id: int, is_active: bool) -> Optional[PromoCode]:
        return await self._repo.set_active(promo_id, is_active)

    async def list(self, limit: int, offset: int):
        return await self._repo.list_with_usage(limit, offset)

    async def stats(self, promo_id: int, window_days: int) -> Optional[tuple[PromoCode, PromoStats]]:
        promo = await self._repo.get(promo_id)
        if promo is None:
            return None
        return promo, await self._repo.stats(promo, window_days)
