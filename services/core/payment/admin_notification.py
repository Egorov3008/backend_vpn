"""Уведомление админов о каждой успешной оплате.

PaymentRouter после фиксации статуса succeeded сообщает слушателям
(PaymentSuccessListener) об оплате. AdminPaymentNotifier — один такой слушатель:
шлёт каждому админу из ADMIN_ID сообщение «Услуга / Сумма».

Сбой уведомления не должен ломать обработку платежа: ошибки отправки
логируются и глотаются здесь же, а роутер дополнительно страхуется сам.
"""

import html
from dataclasses import dataclass
from typing import Iterable, Optional, Protocol

from logger import logger


@dataclass(frozen=True)
class PaidPayment:
    """Успешно обработанный платёж — то, что видят слушатели."""
    payment_id: str
    amount: float
    tg_id: int
    operation: str
    # Описание платежа так, как его видит ЮKassa (поле description счёта).
    # None — описание недоступно (например, ручной sweep не передал его).
    description: Optional[str] = None


class MessageSender(Protocol):
    """Отправка текста в чат (TelegramBotNotifier.send_message подходит как есть)."""

    async def send_message(self, chat_id: int, text: str) -> None: ...


class PaymentSuccessListener(Protocol):
    """Реакция на успешную оплату (вызывается после фиксации succeeded)."""

    async def on_payment_succeeded(self, payment: PaidPayment) -> None: ...


def format_admin_payment_message(payment: PaidPayment) -> str:
    """Текст для админа: услуга (описание из кассы + ID платежа) и сумма. HTML."""
    description = (payment.description or "").strip() or "без описания"
    return (
        "💰 <b>Новая оплата</b>\n\n"
        f"Услуга: {html.escape(description)} — ID <code>{html.escape(payment.payment_id)}</code>\n"
        f"Сумма: <b>{payment.amount:.2f} ₽</b>"
    )


class AdminPaymentNotifier:
    """Шлёт уведомление об оплате каждому админу."""

    def __init__(self, notifier: MessageSender, admin_ids: Iterable[int]):
        self._notifier = notifier
        self._admin_ids = tuple(admin_ids)

    async def on_payment_succeeded(self, payment: PaidPayment) -> None:
        if not self._admin_ids:
            logger.debug("Уведомление админов об оплате пропущено: ADMIN_ID пуст", payment_id=payment.payment_id)
            return

        text = format_admin_payment_message(payment)
        for admin_id in self._admin_ids:
            try:
                await self._notifier.send_message(admin_id, text)
            except Exception as e:
                logger.error(
                    "Не удалось уведомить админа об оплате",
                    payment_id=payment.payment_id,
                    admin_id=admin_id,
                    error_type=type(e).__name__,
                    error=str(e),
                )
