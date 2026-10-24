"""Правила проверки промокода: по одному классу на правило.

Правило возвращает причину отказа (строка из ``Reason``) или None. Правила не
меняют данные; факты из БД берут через ``PromoFacts``, поэтому одни и те же
правила работают и на расчёте цены, и внутри транзакции резерва.
Новое правило добавляется новым классом в ``DEFAULT_RULES``.
"""
from datetime import datetime
from typing import Optional, Protocol, Sequence

from services.core.promo_codes.models import PromoCode, PromoRequest


class Reason:
    NOT_FOUND = "not_found"
    INACTIVE = "inactive"
    NOT_STARTED = "not_started"
    EXPIRED = "expired"
    WRONG_OPERATION = "wrong_operation"
    NOT_NEW_USER = "not_new_user"
    ALREADY_USED = "already_used"
    LIMIT_REACHED = "limit_reached"
    TOO_MANY_ATTEMPTS = "too_many_attempts"


class PromoFacts(Protocol):
    """Факты о пользователе и коде, нужные правилам."""

    async def has_paid(self, tg_id: int) -> bool: ...

    async def user_has_used(self, promo_id: int, tg_id: int) -> bool: ...

    async def taken_by_others(self, promo_id: int, tg_id: int) -> int: ...


class PromoRule(Protocol):
    async def check(
        self, promo: PromoCode, request: PromoRequest, facts: PromoFacts, now: datetime
    ) -> Optional[str]: ...


class OwnerRule:
    """Личный код видит только владелец; для остальных его как будто нет."""

    async def check(self, promo, request, facts, now):
        if promo.owner_tg_id is not None and promo.owner_tg_id != request.tg_id:
            return Reason.NOT_FOUND
        return None


class ActiveRule:
    async def check(self, promo, request, facts, now):
        return None if promo.is_active else Reason.INACTIVE


class WindowRule:
    async def check(self, promo, request, facts, now):
        if now < promo.starts_at:
            return Reason.NOT_STARTED
        if now >= promo.ends_at:
            return Reason.EXPIRED
        return None


class OperationRule:
    async def check(self, promo, request, facts, now):
        if promo.operations != "any" and promo.operations != request.operation:
            return Reason.WRONG_OPERATION
        return None


class AudienceRule:
    """``new`` — только тем, у кого ещё не было успешной оплаты (пробный ключ не в счёт)."""

    async def check(self, promo, request, facts, now):
        if promo.audience == "new" and await facts.has_paid(request.tg_id):
            return Reason.NOT_NEW_USER
        return None


class OncePerUserRule:
    async def check(self, promo, request, facts, now):
        if await facts.user_has_used(promo.id, request.tg_id):
            return Reason.ALREADY_USED
        return None


class LimitRule:
    """Лимит активаций: оплаченные и свежие резервы других пользователей.

    Свой резерв пользователя не считается: повторное нажатие «оплатить» с тем же
    кодом не должно упираться в собственную бронь.
    """

    async def check(self, promo, request, facts, now):
        if promo.max_redemptions is None:
            return None
        if await facts.taken_by_others(promo.id, request.tg_id) >= promo.max_redemptions:
            return Reason.LIMIT_REACHED
        return None


# Порядок важен: дешёвые проверки без БД идут первыми, OwnerRule — самым первым,
# чтобы чужой личный код не раскрывал ни срок, ни лимит.
DEFAULT_RULES: Sequence[PromoRule] = (
    OwnerRule(),
    ActiveRule(),
    WindowRule(),
    OperationRule(),
    AudienceRule(),
    OncePerUserRule(),
    LimitRule(),
)


async def first_failure(
    rules: Sequence[PromoRule],
    promo: PromoCode,
    request: PromoRequest,
    facts: PromoFacts,
    now: datetime,
) -> Optional[str]:
    for rule in rules:
        reason = await rule.check(promo, request, facts, now)
        if reason is not None:
            return reason
    return None
