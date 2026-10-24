"""Проверка и резерв промокода для расчёта цены и создания счёта."""
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Callable, Deque, Dict, Optional, Sequence

from services.core.promo_codes.models import PromoCheck, PromoCode, PromoRequest
from services.core.promo_codes.repository import PromoCodeRepository
from services.core.promo_codes.rules import DEFAULT_RULES, PromoRule, Reason, first_failure

# Латиница, цифры, дефис и подчёркивание. Код хранится и сравнивается в верхнем регистре.
CODE_PATTERN = re.compile(r"^[A-Z0-9_-]{4,32}$")

# Тексты для пользователя: клиенты (Mini App, сайт) показывают их как есть.
MESSAGES: Dict[str, str] = {
    Reason.NOT_FOUND: "Такого промокода нет",
    Reason.INACTIVE: "Промокод больше не действует",
    Reason.NOT_STARTED: "Промокод ещё не начал действовать",
    Reason.EXPIRED: "Срок действия промокода закончился",
    Reason.WRONG_OPERATION: "Промокод не подходит для этой оплаты",
    Reason.NOT_NEW_USER: "Промокод только для первой покупки",
    Reason.ALREADY_USED: "Вы уже использовали этот промокод",
    Reason.LIMIT_REACHED: "Промокоды закончились",
    Reason.TOO_MANY_ATTEMPTS: "Слишком много попыток. Попробуйте позже",
}
NOT_BETTER_MESSAGE = "У вас уже есть скидка больше, промокод не нужен"
APPLIED = "applied"
NOT_BETTER = "not_better"


def normalize_code(code: str) -> str:
    return code.strip().upper()


class FailedAttemptLimiter:
    """Не больше ``max_attempts`` неверных кодов на пользователя за ``window_seconds``.

    Защищает от перебора кодов с разных IP одним аккаунтом. Состояние в памяти
    процесса, как у ``app/rate_limit.py`` (бэкенд работает одним процессом).
    """

    def __init__(self, max_attempts: int, window_seconds: int, clock: Callable[[], float] = time.monotonic):
        self._max = max_attempts
        self._window = window_seconds
        self._clock = clock
        self._failures: Dict[int, Deque[float]] = defaultdict(deque)

    def _bucket(self, tg_id: int) -> Deque[float]:
        bucket = self._failures[tg_id]
        start = self._clock() - self._window
        while bucket and bucket[0] < start:
            bucket.popleft()
        return bucket

    def blocked(self, tg_id: int) -> bool:
        return len(self._bucket(tg_id)) >= self._max

    def record_failure(self, tg_id: int) -> None:
        self._bucket(tg_id).append(self._clock())
        if len(self._failures) > 10_000:
            for key in [k for k, v in self._failures.items() if not v]:
                self._failures.pop(key, None)

    def clear(self) -> None:
        self._failures.clear()


class PromoCodeService:
    def __init__(
        self,
        repository: PromoCodeRepository,
        limiter: FailedAttemptLimiter,
        rules: Sequence[PromoRule] = DEFAULT_RULES,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self._repo = repository
        self._limiter = limiter
        self._rules = rules
        self._now = now

    async def check(self, code: str, request: PromoRequest) -> PromoCheck:
        """Подходит ли код этому пользователю для этой оплаты (без резерва)."""
        if self._limiter.blocked(request.tg_id):
            return PromoCheck(reason=Reason.TOO_MANY_ATTEMPTS)
        normalized = normalize_code(code)
        promo = await self._repo.get_by_code(normalized) if CODE_PATTERN.match(normalized) else None
        if promo is None:
            self._limiter.record_failure(request.tg_id)
            return PromoCheck(reason=Reason.NOT_FOUND)
        reason = await first_failure(self._rules, promo, request, self._repo.facts(), self._now())
        if reason == Reason.NOT_FOUND:
            # Чужой личный код — для перебора то же, что несуществующий
            self._limiter.record_failure(request.tg_id)
        return PromoCheck(promo=None if reason else promo, reason=reason)

    async def reserve(self, promo: PromoCode, request: PromoRequest, discount_amount: float) -> Optional[str]:
        """Занять активацию под счёт; None — успешно, иначе причина отказа."""
        return await self._repo.reserve(promo.id, request, discount_amount, self._rules, self._now())

    async def attach_payment(self, payment_id: str, promo: PromoCode, tg_id: int, discount_amount: float) -> None:
        await self._repo.attach_payment(payment_id, promo.id, tg_id, discount_amount)


def message_for(reason: Optional[str]) -> Optional[str]:
    if reason is None:
        return None
    return MESSAGES.get(reason, MESSAGES[Reason.NOT_FOUND])
