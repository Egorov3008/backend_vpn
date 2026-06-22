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

from dataclasses import dataclass, field
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
async def test_balance_discount_is_deducted_from_user_balance():
    """When the user paid using their reward balance, that amount
    must be subtracted from users.balance after a successful payment."""
    user = make_user(tg_id=100, balance=50.0)
    payment = make_payment(
        tg_id=100,
        amount=160.0,                # final amount after 40₽ discount
        referral_discount=0.0,       # no 10% referred-customer discount
        balance_discount=40.0,       # 40₽ came from user's reward balance
    )
    processor, router, model_service, _ = make_processor_and_router(payment, user)

    await router.route(payment.payment_id)

    assert user.balance == pytest.approx(10.0, abs=1e-9), (
        f"Expected user.balance=10.0 after deducting 40₽ balance discount, "
        f"got {user.balance}"
    )
    model_service.users.update.assert_awaited_once()


@pytest.mark.asyncio
async def test_balance_discount_not_deducted_twice_for_idempotent_reroute():
    """If the webhook re-fires after a successful payment, the early-return
    for status=='succeeded' must prevent a second deduction."""
    user = make_user(tg_id=100, balance=10.0)
    payment = make_payment(
        tg_id=100,
        amount=160.0,
        referral_discount=0.0,
        balance_discount=40.0,
        status="succeeded",          # already processed
    )
    processor, router, model_service, _ = make_processor_and_router(payment, user)

    await router.route(payment.payment_id)

    # User balance must remain untouched.
    assert user.balance == pytest.approx(10.0, abs=1e-9)
    model_service.users.update.assert_not_awaited()


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
async def test_processor_loads_balance_discount_from_payment():
    """PaymentProcessor must surface balance_discount from the persisted
    PaymentModel so the router can deduct it."""
    payment = make_payment(balance_discount=42.5, referral_discount=0.0)
    processor, _, model_service, _ = make_processor_and_router(payment, make_user())

    await processor.load_payment_data(payment.payment_id)

    assert processor.balance_discount == pytest.approx(42.5, abs=1e-9)
    assert processor.referral_discount == pytest.approx(0.0, abs=1e-9)


@pytest.mark.asyncio
async def test_update_payment_preserves_discount_fields():
    """When the processor updates a payment status, it must preserve the
    discount fields (referral_discount, balance_discount) so the persisted
    payment record is not corrupted with zeros."""
    payment = make_payment(
        payment_id="pay_update",
        tg_id=400,
        amount=130.0,
        referral_discount=20.0,
        balance_discount=50.0,
        status="pending",
    )
    processor, _, model_service, _ = make_processor_and_router(payment, make_user())
    await processor.load_payment_data(payment.payment_id)

    await processor.update_payment(payment.payment_id, status="succeeded")

    # The update call must include a PaymentModel that still has the discounts.
    model_service.payments.update.assert_awaited_once()
    args, kwargs = model_service.payments.update.await_args
    # args == (conn, payment), kwargs == {"search_data": {...}}
    updated_payment = args[1]
    assert updated_payment.referral_discount == pytest.approx(20.0, abs=1e-9)
    assert updated_payment.balance_discount == pytest.approx(50.0, abs=1e-9)
