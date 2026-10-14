import dataclasses
from typing import Optional

import asyncpg

from logger import logger
from models import PaymentModel
from services.cache import CacheService
from services.cache.key_manager import CacheKeyManager
from services.core.data.service import ServiceDataModel


class PaymentProcessor:
    """Общая логика обработки платежа: загрузка данных, валидация, обновление."""

    def __init__(
        self,
        conn: asyncpg.Pool,
        model_service: ServiceDataModel,
        cache: CacheService,
    ):
        self._conn = conn
        self._model_service = model_service
        self._cache = cache
        # Строка платежа, загруженная из БД: основа для обновления кеша (сохраняет created_at, id)
        self._payment: Optional[PaymentModel] = None

        # Поля, заполняемые при обработке
        self.amount: float = None
        self.payment_type: str = None
        self.tg_id: int = None
        self.number_of_months: int = None
        self.discount_percent: int = 0
        self.referral_discount: float = 0.0
        self.balance_discount: float = 0.0
        self.status: str = None

    async def load_payment_data(self, payment_id: str):
        """Загружает данные платежа из БД."""
        logger.debug("Загрузка данных платежа из БД", payment_id=payment_id)

        data: PaymentModel = await self._model_service.payments.get_data(payment_id, self._conn)

        if not data:
            logger.error("Платёж не найден в БД", payment_id=payment_id)
            raise ValueError(f"Платёж не найден: {payment_id}")

        self.amount = data.amount
        self.payment_type = data.payment_type
        self.tg_id = data.tg_id
        self.number_of_months = data.number_of_months
        self.discount_percent = data.discount_percent
        self.referral_discount = data.referral_discount
        self.balance_discount = data.balance_discount
        self.status = data.status
        self._payment = data

        logger.debug(
            "Данные платежа успешно загружены",
            payment_id=payment_id,
            tg_id=self.tg_id,
            amount=self.amount,
            status=self.status,
            payment_type=self.payment_type,
            number_of_months=self.number_of_months,
            discount_percent=self.discount_percent,
            referral_discount=self.referral_discount,
            balance_discount=self.balance_discount,
        )

    async def refresh_payment_cache(self, payment_id: str, status: str = "succeeded") -> None:
        """Обновляет только кеш платежа после того, как статус уже записан в БД атомарно
        (complete_claim). Берём загруженную строку и меняем только статус: так сохраняются
        created_at и id, которых нет в полях процессора. Полная перезапись строки здесь
        не нужна, и её сбой не должен прерывать уведомление, списание и бонус — best-effort.
        """
        if self._payment is None or self._payment.payment_id != payment_id:
            logger.warning("Кеш платежа не обновлён: строка не загружена", payment_id=payment_id)
            return
        payment = dataclasses.replace(self._payment, status=status)
        self._payment = payment
        try:
            await self._cache.payments.set(CacheKeyManager.payment(payment_id), payment)
        except Exception as e:
            logger.warning(
                "Не удалось обновить кеш платежа после обработки",
                payment_id=payment_id,
                error_type=type(e).__name__,
                error=str(e),
            )

    async def claim_payment(self, payment_id: str) -> Optional[str]:
        """Атомарно захватывает платёж для обработки (pending -> processing).

        Возвращает claim-token владельца захвата; None — платёж уже захвачен другим
        процессом или не в статусе pending. Токен нужен для release/complete.
        """
        return await self._model_service.payments.service.claim_pending(self._conn, payment_id)

    async def release_claim(self, payment_id: str, token: str) -> bool:
        """Снимает захват (processing -> pending), чтобы платёж мог быть обработан повторно.

        False — захват уже перехвачен другим владельцем, ничего не сброшено.
        """
        return await self._model_service.payments.service.release_claim(
            self._conn, payment_id, token
        )

    async def complete_claim(self, payment_id: str, token: str) -> bool:
        """Переводит захваченный платёж в succeeded. False — захват уже не наш."""
        return await self._model_service.payments.service.complete_claim(
            self._conn, payment_id, token
        )

    def extract_operation(self) -> list[str]:
        """Извлекает тип операции и данные из payment_type."""
        logger.debug("Извлечение операции из payment_type", payment_type=self.payment_type)
        if "|" not in self.payment_type:
            logger.error("Некорректный формат payment_type (нет разделителя |)", payment_type=self.payment_type)
            raise ValueError(f"Некорректный формат payment_type: {self.payment_type}")
        operation, data = self.payment_type.split("|", 1)
        logger.debug("Операция извлечена успешно", operation=operation, data=data)
        return [operation, data]
