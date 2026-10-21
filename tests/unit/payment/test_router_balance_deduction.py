"""
Regression tests for the balance-deduction bug in PaymentRouter.

Bug: after a successful payment that used the user's referral balance
(`balance_discount`), the discount was NOT deducted from `users.balance`.
As a result, on subsequent payment attempts the discount was applied again
because the balance still showed the pre-payment value.

Root cause: `router.py` was deducting `self.processor.referral_discount`
(which holds the 10% referred-customer discount) instead of
`self.processor.balance_discount` (the amount actually subtracted from
the price via the user's reward balance).
"""

from datetime import datetime
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from models import PaymentModel
from services.core.payment.processor import PaymentProcessor
from services.core.payment.router import PaymentRouter


# ---------------------------------------------------------------------------
# Helpers — minimal fakes so the test does not need the full DI graph.
# ---------------------------------------------------------------------------


def make_payment(
    payment_id: str = "pay_1",
    tg_id: int = 100,
    amount: float = 200.0,
    referral_discount: float = 0.0,
    balance_discount: float = 0.0,
    status: str = "pending",
    payment_type: str = "create_key|1",
) -> PaymentModel:
    return PaymentModel(
        payment_id=payment_id,
        tg_id=tg_id,
        amount=amount,
        payment_type=payment_type,
        status=status,
        number_of_months=1,
        referral_discount=referral_discount,
        balance_discount=balance_discount,
        created_at=datetime.now(),
    )


def make_user(tg_id: int = 100, balance: float = 50.0) -> MagicMock:
    user = MagicMock()
    user.tg_id = tg_id
    user.balance = balance
    return user


def make_processor_and_router(
    payment: PaymentModel, user: Optional[MagicMock]
) -> tuple[PaymentProcessor, PaymentRouter, MagicMock, MagicMock]:
    """Build a (processor, router, model_service, cache) tuple."""
    model_service = MagicMock()
    model_service.payments.get_data = AsyncMock(return_value=payment)
    model_service.payments.update = AsyncMock(return_value=None)
    model_service.payments.service.claim_pending = AsyncMock(return_value="claim-token")
    model_service.payments.service.release_claim = AsyncMock(return_value=True)
    model_service.payments.service.complete_claim = AsyncMock(return_value=True)
    model_service.payments.save_data = AsyncMock(return_value=None)

    if user is not None:
        model_service.users.get_data = AsyncMock(return_value=user)
        model_service.users.update = AsyncMock(return_value=None)
    else:
        model_service.users.get_data = AsyncMock(return_value=None)
        model_service.users.update = AsyncMock(return_value=None)

    cache = MagicMock()

    conn = MagicMock()

    processor = PaymentProcessor(conn=conn, model_service=model_service, cache=cache)

    # creation/renewal/bonus services are not exercised by the bug —
    # short-circuit their `.process(...)` and notification calls.
    creation_service = MagicMock()
    creation_service.process = AsyncMock(return_value={"key": "k"})
    creation_service.send_notification = AsyncMock(return_value=None)

    renewal_service = MagicMock()
    renewal_service.process = AsyncMock(return_value=None)
    renewal_service.send_notification = AsyncMock(return_value=None)

    bonus_service = MagicMock()
    bonus_service.process_referral_bonus = AsyncMock(return_value=None)

    router = PaymentRouter(
        processor=processor,
        creation_service=creation_service,
        renewal_service=renewal_service,
        bonus_service=bonus_service,
    )
    return processor, router, model_service, cache


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_referral_10pct_discount_does_not_touch_user_balance():
    """The 10% referred-customer discount must NOT reduce users.balance —
    it is a price reduction for the new customer, not a spend of the
    reward balance."""
    user = make_user(tg_id=200, balance=0.0)
    payment = make_payment(
        tg_id=200,
        amount=180.0,                # 200 - 20₽ 10% referral discount
        referral_discount=20.0,      # 10% discount applied
        balance_discount=0.0,        # nothing from balance
    )
    processor, router, model_service, _ = make_processor_and_router(payment, user)

    await router.route(payment.payment_id)

    # Balance must stay at 0 (cannot go negative).
    assert user.balance == pytest.approx(0.0, abs=1e-9)
    model_service.users.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_combined_discounts_only_balance_part_is_deducted():
    """When BOTH discounts apply, only the balance part must be deducted."""
    user = make_user(tg_id=300, balance=100.0)
    payment = make_payment(
        tg_id=300,
        amount=130.0,                # 200 - 20₽ referral - 50₽ balance
        referral_discount=20.0,
        balance_discount=50.0,
    )
    processor, router, model_service, _ = make_processor_and_router(payment, user)

    await router.route(payment.payment_id)

    # Only balance_discount (50₽) is deducted; the 10% discount is price-side.
    assert user.balance == pytest.approx(50.0, abs=1e-9)
    model_service.users.update.assert_awaited_once()


@pytest.mark.asyncio
async def test_refresh_cache_preserves_discounts_and_created_at():
    """Обновление кеша после обработки меняет только статус: скидки и created_at
    из загруженной строки сохраняются (иначе история платежей показала бы время оплаты)."""
    payment = make_payment(
        payment_id="pay_update",
        tg_id=400,
        amount=130.0,
        referral_discount=20.0,
        balance_discount=50.0,
        status="pending",
    )
    processor, _, model_service, _ = make_processor_and_router(payment, make_user())
    processor._cache.payments.set = AsyncMock()
    await processor.load_payment_data(payment.payment_id)

    await processor.refresh_payment_cache(payment.payment_id)

    processor._cache.payments.set.assert_awaited_once()
    _, cached = processor._cache.payments.set.await_args.args
    assert cached.status == "succeeded"
    assert cached.referral_discount == pytest.approx(20.0, abs=1e-9)
    assert cached.balance_discount == pytest.approx(50.0, abs=1e-9)
    assert cached.created_at == payment.created_at
    # В БД полная запись строки не пишется
    model_service.payments.update.assert_not_called()
