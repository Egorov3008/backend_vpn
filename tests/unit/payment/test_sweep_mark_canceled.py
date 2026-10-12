"""Sweep отмечает отменённые платежи через условный UPDATE репозитория (не полной перезаписью)."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from models import PaymentModel
from services.core.data.base import BaseData
from services.core.payment.pending_sweep import _mark_canceled


def _payment(status="pending"):
    return PaymentModel(
        payment_id="pay_sw",
        tg_id=7,
        amount=10.0,
        payment_type="create_key|1",
        status=status,
        created_at=datetime(2026, 10, 1, 9, 0, 0),
    )


@pytest.mark.asyncio
async def test_sweep_cancel_uses_conditional_update_and_keeps_created_at():
    # BaseData spec: обращение к несуществующему методу здесь упадёт, как в проде
    payments = MagicMock(spec=BaseData)
    payments.service = MagicMock()
    payments.service.mark_canceled_if_pending = AsyncMock(return_value=True)
    payments.service.get = AsyncMock(return_value=_payment())
    service_data = MagicMock()
    service_data.payments = payments
    cache = MagicMock()
    cache.payments.set = AsyncMock()

    await _mark_canceled(MagicMock(), service_data, cache, "pay_sw")

    payments.service.mark_canceled_if_pending.assert_awaited_once()
    payments.update.assert_not_called()
    cache.payments.set.assert_awaited_once()
    _, cached = cache.payments.set.await_args.args
    assert cached.status == "canceled"
    assert cached.created_at == datetime(2026, 10, 1, 9, 0, 0)


@pytest.mark.asyncio
async def test_sweep_cancel_skipped_when_no_longer_pending():
    payments = MagicMock(spec=BaseData)
    payments.service = MagicMock()
    payments.service.mark_canceled_if_pending = AsyncMock(return_value=False)
    service_data = MagicMock()
    service_data.payments = payments
    cache = MagicMock()
    cache.payments.set = AsyncMock()

    await _mark_canceled(MagicMock(), service_data, cache, "pay_sw")

    cache.payments.set.assert_not_called()
