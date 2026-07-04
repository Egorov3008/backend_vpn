"""
Regression test: PaymentRouter.route must invoke referral bonus service.

Ensures that after a successful payment, the wired ReferralBonusService
processes the referral bonus for the paying user.
"""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from models import PaymentModel
from services.core.payment.processor import PaymentProcessor
from services.core.payment.router import PaymentRouter


def _make_payment(
    payment_id: str = "pay_bonus",
    tg_id: int = 200,
    amount: float = 500.0,
    status: str = "pending",
) -> PaymentModel:
    return PaymentModel(
        payment_id=payment_id,
        tg_id=tg_id,
        amount=amount,
        payment_type="create_key|1",
        status=status,
        number_of_months=1,
        referral_discount=0.0,
        balance_discount=0.0,
        created_at=datetime.now(),
    )


@pytest.mark.asyncio
async def test_route_calls_referral_bonus_service():
    """After successful payment route, bonus_service.process_referral_bonus
    must be called with the paying user's tg_id and payment amount."""
    payment = _make_payment()

    model_service = MagicMock()
    model_service.payments.get_data = AsyncMock(return_value=payment)
    model_service.payments.update = AsyncMock()
    model_service.users.get_data = AsyncMock(return_value=MagicMock(tg_id=200, balance=0.0))
    model_service.users.update = AsyncMock()

    cache = MagicMock()
    conn = MagicMock()

    processor = PaymentProcessor(conn=conn, model_service=model_service, cache=cache)

    creation_service = MagicMock()
    creation_service.process = AsyncMock(return_value={"key": "k"})
    creation_service.send_notification = AsyncMock()

    renewal_service = MagicMock()
    renewal_service.process = AsyncMock(return_value=None)
    renewal_service.send_notification = AsyncMock()

    bonus_service = MagicMock()
    bonus_service.process_referral_bonus = AsyncMock(return_value=True)

    router = PaymentRouter(
        processor=processor,
        creation_service=creation_service,
        renewal_service=renewal_service,
        bonus_service=bonus_service,
    )

    await router.route(payment.payment_id)

    bonus_service.process_referral_bonus.assert_awaited_once()
    args, kwargs = bonus_service.process_referral_bonus.await_args
    assert kwargs["referred_tg_id"] == 200
    assert kwargs["payment_amount"] == 500.0
