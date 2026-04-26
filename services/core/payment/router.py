from typing import Optional

from logger import logger
from services.core.payment.creation_service import KeyCreationService
from services.core.payment.processor import PaymentProcessor
from services.core.payment.renewal_service import KeyRenewalService
from services.core.referral.bonus_service import ReferralBonusService


class PaymentRouter:
    """Маршрутизация платежа на нужный обработчик."""

    def __init__(
        self,
        processor: PaymentProcessor,
        creation_service: KeyCreationService,
        renewal_service: KeyRenewalService,
        bonus_service: Optional[ReferralBonusService] = None,
    ):
        self.processor = processor
        self.creation_service = creation_service
        self.renewal_service = renewal_service
        self.bonus_service = bonus_service

    async def route(self, payment_id: str):
        """Основной метод маршрутизации."""
        logger.info(
            "Начало обработки платежа",
            payment_id=payment_id,
        )
        
        await self.processor.load_payment_data(payment_id)

        # Идемпотентность: пропускаем уже обработанный платёж
        if self.processor.status == "succeeded":
            logger.info(
                "Платёж уже обработан, пропуск",
                payment_id=payment_id,
                tg_id=self.processor.tg_id,
                amount=self.processor.amount,
                payment_type=self.processor.payment_type,
            )
            return

        operation, data = self.processor.extract_operation()

        if operation == "create_key":
            logger.info(
                "Обработка операции создания ключа",
                payment_id=payment_id,
                tariff_id=data,
                tg_id=self.processor.tg_id,
            )
            await self.creation_service.process(tariff_id=data)
        elif operation == "renew_key":
            logger.info(
                "Обработка операции продления ключа",
                payment_id=payment_id,
                email=data,
                tg_id=self.processor.tg_id,
                amount=self.processor.amount,
            )
            await self.renewal_service.process(email=data)
        else:
            logger.error(
                "Неизвестный тип операции",
                payment_id=payment_id,
                operation=operation,
                data=data,
            )
            raise ValueError(f"Неизвестный тип операции: {operation}")

        await self.processor.update_payment(payment_id)
        
        logger.info(
            "Платеж успешно обработан",
            payment_id=payment_id,
            tg_id=self.processor.tg_id,
            amount=self.processor.amount,
            operation=operation,
        )

        # Списываем реферальную скидку с баланса пользователя
        if self.processor.referral_discount and self.processor.referral_discount > 0:
            try:
                user = await self.processor._model_service.users.get_data(
                    self.processor.tg_id
                )
                if user:
                    user.balance = round(
                        max(0.0, user.balance - self.processor.referral_discount), 2
                    )
                    await self.processor._model_service.users.update(
                        self.processor._conn, user,
                        search_data={"tg_id": user.tg_id},
                    )
                    logger.info(
                        "Реферальная скидка списана",
                        payment_id=payment_id,
                        tg_id=self.processor.tg_id,
                        discount=self.processor.referral_discount,
                        new_balance=user.balance,
                    )
            except Exception as e:
                logger.warning(
                    "Ошибка при списании реферальной скидки",
                    payment_id=payment_id,
                    error=str(e),
                )

        # Начисляем реферальный бонус после успешной оплаты
        if self.bonus_service:
            try:
                await self.bonus_service.process_referral_bonus(
                    conn=self.processor._conn,
                    referred_tg_id=self.processor.tg_id,
                    payment_amount=self.processor.amount,
                )
                logger.info(
                    "Реферальный бонус начислен",
                    payment_id=payment_id,
                    referred_tg_id=self.processor.tg_id,
                    payment_amount=self.processor.amount,
                )
            except Exception as e:
                logger.warning(
                    "Ошибка при начислении реферального бонуса",
                    payment_id=payment_id,
                    error=str(e),
                )
