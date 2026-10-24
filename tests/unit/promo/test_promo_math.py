"""Чистая логика промокодов: размер скидки и счётчик неверных кодов."""
from datetime import datetime, timezone

import pytest

from services.core.promo_codes.models import PromoCode
from services.core.promo_codes.service import FailedAttemptLimiter


def _promo(percent=30, max_months=None) -> PromoCode:
    now = datetime.now(timezone.utc)
    return PromoCode(
        id=1, code="X", discount_percent=percent, starts_at=now, ends_at=now, max_redemptions=None,
        audience="all", operations="any", max_months=max_months, owner_tg_id=None, is_active=True,
    )


@pytest.mark.parametrize("months, max_months, discounted", [(1, None, 1), (3, None, 3), (3, 1, 1), (2, 6, 2)])
def test_discount_covers_only_allowed_months(months, max_months, discounted):
    price = 150.0
    assert _promo(max_months=max_months).discount_for(price, months) == pytest.approx(price * discounted * 0.30)


def test_limiter_blocks_after_max_failures_and_forgets_old_ones():
    now = [0.0]
    limiter = FailedAttemptLimiter(max_attempts=2, window_seconds=60, clock=lambda: now[0])

    limiter.record_failure(1)
    limiter.record_failure(1)

    assert limiter.blocked(1)
    assert not limiter.blocked(2)
    now[0] = 61.0
    assert not limiter.blocked(1)
