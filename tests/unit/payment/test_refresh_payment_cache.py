"""PaymentProcessor.refresh_payment_cache: обновляет только кеш и не прерывает обработку."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from models import PaymentModel
from services.cache.key_manager import CacheKeyManager
from services.core.payment.processor import PaymentProcessor


def _payment(payment_id="pay_ok"):
    return PaymentModel(
        payment_id=payment_id,
        tg_id=123,
        amount=99.0,
        payment_type="create_key|1",
        number_of_months=1,
        status="pending",
        created_at=datetime(2026, 10, 1, 9, 0, 0),
    )


async def _loaded_processor(cache, payment):
    model_service = MagicMock()
    model_service.payments.get_data = AsyncMock(return_value=payment)
    model_service.payments.update = AsyncMock()
    proc = PaymentProcessor(conn=MagicMock(), model_service=model_service, cache=cache)
    await proc.load_payment_data(payment.payment_id)
    return proc


@pytest.mark.asyncio
async def test_refresh_sets_cache_with_succeeded_status_and_no_db_write():
    cache = MagicMock()
    cache.payments.set = AsyncMock()
    proc = await _loaded_processor(cache, _payment("pay_ok"))

    await proc.refresh_payment_cache("pay_ok")

    cache.payments.set.assert_awaited_once()
    key, model = cache.payments.set.await_args.args
    assert key == CacheKeyManager.payment("pay_ok")
    assert model.status == "succeeded"
    assert model.tg_id == 123
    # created_at и id берутся из загруженной строки, а не заново (иначе история платежей врала бы)
    assert model.created_at == datetime(2026, 10, 1, 9, 0, 0)
    # Полной перезаписи строки в БД нет: статус уже записан complete_claim
    proc._model_service.payments.update.assert_not_called()


@pytest.mark.asyncio
async def test_refresh_failure_does_not_raise():
    cache = MagicMock()
    cache.payments.set = AsyncMock(side_effect=RuntimeError("cache down"))
    proc = await _loaded_processor(cache, _payment("pay_fail"))

    # Не должно бросать исключение: сбой кеша не отменяет уведомление и бонусы
    await proc.refresh_payment_cache("pay_fail")


@pytest.mark.asyncio
async def test_refresh_without_loaded_row_is_noop():
    cache = MagicMock()
    cache.payments.set = AsyncMock()
    proc = PaymentProcessor(conn=MagicMock(), model_service=MagicMock(), cache=cache)

    await proc.refresh_payment_cache("pay_none")

    cache.payments.set.assert_not_called()
