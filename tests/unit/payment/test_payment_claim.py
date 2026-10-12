"""Гонка вебхука YooKassa и поллинга мини-аппа по одному платежу.

Раньше оба пути проверяли статус 'pending' и оба создавали ключ. Здесь БД
имитируется in-memory с атомарным захватом, как в PaymentRepository.claim_pending.
"""

import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from models import PaymentModel
from services.core.payment.processor import PaymentProcessor
from services.core.payment.router import PaymentRouter


class FakePaymentsDB:
    """Минимальная БД платежей: статус + атомарный захват (под asyncio.Lock)."""

    def __init__(self, payment: PaymentModel):
        self.payment = payment
        self.token = None
        # True имитирует прошедшие PAYMENT_CLAIM_STALE_MINUTES: захват можно перехватить.
        self.stale = False
        self._lock = asyncio.Lock()

    async def get_data(self, payment_id, conn=None):
        # Возвращаем копию: как из БД, а не ссылку на общий объект.
        return PaymentModel(**self.payment.__dict__)

    async def claim_pending(self, conn, payment_id):
        async with self._lock:
            if self.payment.payment_id != payment_id:
                return None
            if self.payment.status == "pending" or (self.payment.status == "processing" and self.stale):
                self.payment.status = "processing"
                self.token = str(uuid.uuid4())
                self.stale = False
                return self.token
            return None

    async def release_claim(self, conn, payment_id, token):
        async with self._lock:
            if self._owns(payment_id, token):
                self.payment.status = "pending"
                self.token = None
                return True
            return False

    async def complete_claim(self, conn, payment_id, token):
        async with self._lock:
            if self._owns(payment_id, token):
                self.payment.status = "succeeded"
                self.token = None
                return True
            return False

    def _owns(self, payment_id, token) -> bool:
        return (
            self.payment.payment_id == payment_id
            and self.payment.status == "processing"
            and token is not None
            and token == self.token
        )

    async def update(self, conn, payment, search_data=None):
        async with self._lock:
            self.payment.status = payment.status


def build(payment_db: FakePaymentsDB, creation_process):
    model_service = MagicMock()
    model_service.payments = MagicMock()
    model_service.payments.get_data = payment_db.get_data
    model_service.payments.update = payment_db.update
    model_service.payments.service = payment_db
    model_service.users.get_data = AsyncMock(return_value=None)
    model_service.users.update = AsyncMock()

    processor = PaymentProcessor(conn=MagicMock(), model_service=model_service, cache=MagicMock())

    creation_service = MagicMock()
    creation_service.process = creation_process
    creation_service.send_notification = AsyncMock()

    renewal_service = MagicMock()
    renewal_service.process = AsyncMock(return_value=None)
    renewal_service.send_notification = AsyncMock()

    bonus_service = MagicMock()
    bonus_service.process_referral_bonus = AsyncMock(return_value=False)

    router = PaymentRouter(
        processor=processor,
        creation_service=creation_service,
        renewal_service=renewal_service,
        bonus_service=bonus_service,
    )
    return router, creation_service


def make_payment() -> PaymentModel:
    return PaymentModel(
        payment_id="pay_race",
        tg_id=552810834,
        amount=199.0,
        payment_type="create_key|2",
        status="pending",
        number_of_months=1,
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_concurrent_routes_create_exactly_one_key():
    """Вебхук и поллинг стартуют одновременно: ключ создаётся ровно один раз."""
    db = FakePaymentsDB(make_payment())
    created = []

    async def slow_create(tariff_id=None, payment_id=None):
        # Имитируем долгий вызов 3x-UI (в проде ~12-17 с).
        await asyncio.sleep(0.05)
        created.append(payment_id)
        return {"public_link": "k", "days": 29, "link_to_connect": "k", "email": "e"}

    router, creation_service = build(db, AsyncMock(side_effect=slow_create))

    await asyncio.gather(router.route("pay_race"), router.route("pay_race"))

    assert created == ["pay_race"]
    assert creation_service.send_notification.await_count == 1
    assert db.payment.status == "succeeded"


@pytest.mark.asyncio
async def test_claim_lost_skips_everything():
    """Если платёж уже захвачен, роутер не создаёт ключ и не меняет статус."""
    db = FakePaymentsDB(make_payment())
    db.payment.status = "processing"
    creation = AsyncMock(return_value={"public_link": "k"})
    router, creation_service = build(db, creation)

    await router.route("pay_race")

    creation.assert_not_called()
    creation_service.send_notification.assert_not_called()
    assert db.payment.status == "processing"


@pytest.mark.asyncio
async def test_failure_releases_claim_and_retry_succeeds():
    """Сбой создания ключа возвращает платёж в pending, повтор проходит штатно."""
    db = FakePaymentsDB(make_payment())
    creation = AsyncMock(side_effect=[
        RuntimeError("3x-UI down"),
        {"public_link": "k", "days": 29, "link_to_connect": "k", "email": "e"},
    ])
    router, creation_service = build(db, creation)

    with pytest.raises(RuntimeError):
        await router.route("pay_race")
    assert db.payment.status == "pending"

    await router.route("pay_race")
    assert db.payment.status == "succeeded"
    assert creation.await_count == 2
    creation_service.send_notification.assert_awaited_once()


@pytest.mark.asyncio
async def test_already_created_key_is_notified_on_retry():
    """Повтор по платежу, за который ключ уже выдан: статус закрываем и доставляем уведомление.

    Уведомление шлётся только после complete_claim, значит предыдущая попытка до пользователя
    не дошла; повторно route по succeeded-платежу не идёт (ранний return).
    """
    db = FakePaymentsDB(make_payment())
    creation = AsyncMock(return_value={
        "public_link": "k", "days": 29, "link_to_connect": "k", "email": "e",
        "already_created": True,
    })
    router, creation_service = build(db, creation)

    await router.route("pay_race")

    creation.assert_awaited_once()
    assert creation.await_args.kwargs["payment_id"] == "pay_race"
    creation_service.send_notification.assert_awaited_once()
    assert db.payment.status == "succeeded"


@pytest.mark.asyncio
async def test_evicted_owner_cannot_release_or_complete_new_claim():
    """Прежний владелец после перехвата по таймауту не трогает захват нового."""
    db = FakePaymentsDB(make_payment())

    old_token = await db.claim_pending(None, "pay_race")
    db.stale = True  # прошло 10 минут: захват протух
    new_token = await db.claim_pending(None, "pay_race")

    assert old_token and new_token and old_token != new_token
    assert await db.release_claim(None, "pay_race", old_token) is False
    assert await db.complete_claim(None, "pay_race", old_token) is False
    assert db.payment.status == "processing"
    assert await db.complete_claim(None, "pay_race", new_token) is True
    assert db.payment.status == "succeeded"


@pytest.mark.asyncio
async def test_router_stops_when_claim_is_taken_during_processing():
    """Захват перехвачен, пока ключ создаётся: роутер не пишет статус чужим владельцем."""
    db = FakePaymentsDB(make_payment())
    takeover = {}

    async def create_and_lose_claim(tariff_id=None, payment_id=None):
        # Пока мы в 3x-UI, другой процесс перехватывает протухший захват.
        db.stale = True
        takeover["token"] = await db.claim_pending(None, payment_id)
        return {"public_link": "k", "days": 29, "link_to_connect": "k", "email": "e"}

    router, creation_service = build(db, AsyncMock(side_effect=create_and_lose_claim))

    with pytest.raises(RuntimeError):
        await router.route("pay_race")

    assert db.payment.status == "processing"
    assert db.token == takeover["token"]
    creation_service.send_notification.assert_not_called()


@pytest.mark.asyncio
async def test_complete_claim_error_releases_claim_and_retry_succeeds():
    """Сбой записи succeeded (сеть/БД): захват снимается, платёж не висит в processing, повтор проходит."""
    db = FakePaymentsDB(make_payment())
    creation = AsyncMock(return_value={
        "public_link": "k", "days": 29, "link_to_connect": "k", "email": "e",
    })
    router, creation_service = build(db, creation)

    real_complete = db.complete_claim
    attempts = {"n": 0}

    async def flaky_complete(conn, payment_id, token):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ConnectionError("db down")
        return await real_complete(conn, payment_id, token)

    db.complete_claim = flaky_complete

    with pytest.raises(ConnectionError):
        await router.route("pay_race")
    assert db.payment.status == "pending"
    creation_service.send_notification.assert_not_called()

    await router.route("pay_race")
    assert db.payment.status == "succeeded"
    creation_service.send_notification.assert_awaited_once()
