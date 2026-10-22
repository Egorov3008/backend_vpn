"""Уведомление админов об успешной оплате (services/core/payment/admin_notification.py)."""

import pytest

from services.core.payment.admin_notification import AdminPaymentNotifier, PaidPayment
from tests.unit.payment.test_router_balance_deduction import make_payment, make_processor_and_router


class RecordingSender:
    """Заглушка отправки: запоминает сообщения, для указанных чатов падает."""

    def __init__(self, failing_chats=()):
        self.sent: dict[int, list[str]] = {}
        self._failing = set(failing_chats)

    async def send_message(self, chat_id: int, text: str) -> None:
        if chat_id in self._failing:
            raise RuntimeError("telegram down")
        self.sent.setdefault(chat_id, []).append(text)


def _payment(**fields) -> PaidPayment:
    data = dict(payment_id="yk-123", amount=597.0, tg_id=42, operation="create_key",
                description="Помощь в ИТ 42 Стандарт x3")
    data.update(fields)
    return PaidPayment(**data)


async def test_every_admin_gets_service_id_and_amount():
    sender = RecordingSender()
    await AdminPaymentNotifier(sender, [1, 2]).on_payment_succeeded(_payment())

    assert set(sender.sent) == {1, 2}
    for messages in sender.sent.values():
        assert len(messages) == 1
        text = messages[0]
        assert "Помощь в ИТ 42 Стандарт x3" in text
        assert "yk-123" in text
        assert "597" in text


async def test_failed_admin_does_not_block_others():
    sender = RecordingSender(failing_chats={1})
    await AdminPaymentNotifier(sender, [1, 2]).on_payment_succeeded(_payment())

    assert list(sender.sent) == [2]


async def test_description_is_html_escaped():
    sender = RecordingSender()
    await AdminPaymentNotifier(sender, [1]).on_payment_succeeded(_payment(description="<b>x</b> & y"))

    text = sender.sent[1][0]
    assert "<b>x</b>" not in text
    assert "&lt;b&gt;x&lt;/b&gt; &amp; y" in text


async def test_missing_description_still_notifies_with_id():
    sender = RecordingSender()
    await AdminPaymentNotifier(sender, [1]).on_payment_succeeded(_payment(description=None))

    assert "yk-123" in sender.sent[1][0]


async def test_no_admins_sends_nothing():
    sender = RecordingSender()
    await AdminPaymentNotifier(sender, []).on_payment_succeeded(_payment())

    assert sender.sent == {}


class FailingListener:
    async def on_payment_succeeded(self, payment):
        raise RuntimeError("listener broken")


class RecordingListener:
    def __init__(self):
        self.payments: list[PaidPayment] = []

    async def on_payment_succeeded(self, payment):
        self.payments.append(payment)


@pytest.mark.asyncio
async def test_router_survives_broken_listener_and_passes_description():
    payment = make_payment(payment_id="yk-555", amount=199.0)
    _, router, model_service, _ = make_processor_and_router(payment, user=None)
    recorder = RecordingListener()
    router.success_listeners = (FailingListener(), recorder)

    await router.route("yk-555", description="Помощь в ИТ")

    model_service.payments.service.complete_claim.assert_awaited_once()
    router.creation_service.send_notification.assert_awaited_once()
    router.bonus_service.process_referral_bonus.assert_awaited_once()
    assert [(p.payment_id, p.amount, p.description) for p in recorder.payments] == [
        ("yk-555", 199.0, "Помощь в ИТ")
    ]
