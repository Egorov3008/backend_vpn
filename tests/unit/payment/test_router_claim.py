"""PaymentRouter обрабатывает платёж только под эксклюзивным захватом.

Webhook, проверка статуса (Mini App опрашивает её каждые 3 с) и pending-sweep
вызывают route() для одного платежа одновременно. Без захвата оба видят
``pending`` и создают по ключу.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from models import PaymentModel
from services.core.payment.claim import PaymentClaimResult
from services.core.payment.processor import PaymentProcessor
from services.core.payment.router import PaymentRouter, RouteOutcome


class InProcessClaim:
    """Тот же контракт, что у PgAdvisoryPaymentClaim, без Postgres."""

    def __init__(self, db_status="pending"):
        self.held: set[str] = set()
        self.db_status = db_status

    @asynccontextmanager
    async def hold(self, payment_id):
        if payment_id in self.held:
            yield PaymentClaimResult(acquired=False)
            return
        self.held.add(payment_id)
        try:
            yield PaymentClaimResult(acquired=True, db_status=self.db_status)
        finally:
            self.held.discard(payment_id)


def make_router(claim, status="pending", create_delay=0.0):
    payment = PaymentModel(
        payment_id="pay_1", tg_id=100, amount=200.0, payment_type="create_key|1",
        status=status, number_of_months=1, created_at=datetime.now(),
    )
    model_service = MagicMock()
    model_service.payments.get_data = AsyncMock(return_value=payment)
    model_service.payments.update = AsyncMock()
    model_service.payments.save_data = AsyncMock()
    processor = PaymentProcessor(conn=MagicMock(), model_service=model_service, cache=MagicMock())

    async def slow_create(**_):
        await asyncio.sleep(create_delay)
        return {"key": "k"}

    creation = MagicMock()
    creation.process = AsyncMock(side_effect=slow_create)
    creation.send_notification = AsyncMock()
    router = PaymentRouter(
        processor=processor, creation_service=creation, renewal_service=MagicMock(), claim=claim
    )
    return router, creation


@pytest.mark.asyncio
async def test_concurrent_routes_create_one_key():
    claim = InProcessClaim()
    router_a, creation_a = make_router(claim, create_delay=0.05)
    router_b, creation_b = make_router(claim)

    outcomes = await asyncio.gather(router_a.route("pay_1"), router_b.route("pay_1"))

    assert sorted(outcomes) == sorted([RouteOutcome.PROCESSED, RouteOutcome.BUSY])
    assert creation_a.process.await_count + creation_b.process.await_count == 1


@pytest.mark.asyncio
async def test_db_status_succeeded_skips_even_if_cache_says_pending():
    # Другой процесс уже обработал платёж; кеш этого процесса отстал.
    router, creation = make_router(InProcessClaim(db_status="succeeded"), status="pending")

    assert await router.route("pay_1") is RouteOutcome.ALREADY_PROCESSED
    creation.process.assert_not_awaited()


@pytest.mark.asyncio
async def test_claim_released_after_processing_error():
    claim = InProcessClaim()
    router, creation = make_router(claim)
    creation.process.side_effect = RuntimeError("panel down")

    with pytest.raises(RuntimeError):
        await router.route("pay_1")

    assert claim.held == set()
