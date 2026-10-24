"""Отметка «промокод использован» после успешной оплаты."""
from logger import logger
from services.core.payment.admin_notification import PaidPayment
from services.core.promo_codes.repository import PromoCodeRepository


class PromoRedemptionListener:
    """``PaymentSuccessListener``: счёт со скидкой оплачен — активация засчитана.

    Сбой здесь не отменяет оплату (роутер страхует слушателей); резерв просто
    истечёт по TTL, а в статистике кода эта оплата не появится.
    """

    def __init__(self, repository: PromoCodeRepository):
        self._repo = repository

    async def on_payment_succeeded(self, payment: PaidPayment) -> None:
        if await self._repo.mark_used(payment.payment_id, payment.tg_id):
            logger.info("Промокод засчитан", payment_id=payment.payment_id, tg_id=payment.tg_id)
