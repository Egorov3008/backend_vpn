from typing import Optional

from logger import logger
from services.core.payment.creation_service import KeyCreationService
from services.core.payment.processor import PaymentProcessor
from services.core.payment.renewal_service import KeyRenewalService
from services.core.referral.bonus_service import ReferralBonusService


class PaymentRouter:
    """
    Маршрутизация платежа на нужный обработчик.

    Orchestrates payment processing:
    1. Load payment data
    2. Route to creation or renewal service
    3. Send notifications
    4. Handle referral bonuses
    """

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

    async def route(self, payment_id: str) -> None:
        """
        Обработать платеж и отправить уведомления.

        Args:
            payment_id: ID платежа для обработки
        """
        logger.info(
            "Начало обработки платежа",
            payment_id=payment_id,
        )

        logger.debug("Загрузка данных платежа из БД", payment_id=payment_id)
        await self.processor.load_payment_data(payment_id)
        logger.debug(
            "Данные платежа загружены",
            payment_id=payment_id,
            tg_id=self.processor.tg_id,
            status=self.processor.status,
            amount=self.processor.amount,
        )

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

        logger.debug(
            "Извлечение операции из payment_type",
            payment_type=self.processor.payment_type,
        )
        operation, data = self.processor.extract_operation()
        logger.debug("Операция извлечена", operation=operation, data=data)

        try:
            if operation == "create_key":
                logger.info(
                    "Обработка операции создания ключа",
                    payment_id=payment_id,
                    tariff_id=data,
                    tg_id=self.processor.tg_id,
                )
                logger.debug(
                    "Запуск KeyCreationService", tariff_id=data, tg_id=self.processor.tg_id
                )

                # Process creation
                key_data = await self.creation_service.process(tariff_id=data)

                # Send notification
                if key_data:
                    await self.creation_service.send_notification(key_data)

                logger.debug("KeyCreationService завершен", tariff_id=data)

            elif operation == "renew_key":
                logger.info(
                    "Обработка операции продления ключа",
                    payment_id=payment_id,
                    email=data,
                    tg_id=self.processor.tg_id,
                    amount=self.processor.amount,
                )
                logger.debug(
                    "Запуск KeyRenewalService", email=data, tg_id=self.processor.tg_id
                )

                # Process renewal
                renewal_data = await self.renewal_service.process(email=data)

                # Send notification
                if renewal_data:
                    await self.renewal_service.send_notification(
                        updated_key=renewal_data["updated_key"],
                        new_expiry=renewal_data["new_expiry"],
                    )

                logger.debug("KeyRenewalService завершен", email=data)

            else:
                logger.error(
                    "Неизвестный тип операции",
                    payment_id=payment_id,
                    operation=operation,
                    data=data,
                )
                raise ValueError(f"Неизвестный тип операции: {operation}")

        except Exception as e:
            logger.error(
                "Ошибка при обработке платежа (статус остается pending для retry)",
                payment_id=payment_id,
                operation=operation,
                error_type=type(e).__name__,
                error=str(e),
                exc_info=True,
            )
            raise

        logger.debug(
            "Обновление статуса платежа на succeeded", payment_id=payment_id
        )
        await self.processor.update_payment(payment_id)
        logger.debug(
            "Статус платежа обновлен", payment_id=payment_id, new_status="succeeded"
        )

        logger.info(
            "Платеж успешно обработан",
            payment_id=payment_id,
            tg_id=self.processor.tg_id,
            amount=self.processor.amount,
            operation=operation,
            referral_discount=self.processor.referral_discount,
            balance_discount=self.processor.balance_discount,
        )

        # Списываем скидку за счёт баланса (т.е. reward-баланса реферера)
        # именно эту сумму реально вычли из users.balance при расчёте финальной цены.
        # 10% реферальная скидка для приглашённого (referral_discount) не должна
        # уменьшать balance — это скидка от стоимости тарифа, а не списание с баланса.
        if (
            self.processor.balance_discount
            and self.processor.balance_discount > 0
        ):
            try:
                user = await self.processor._model_service.users.get_data(
                    self.processor.tg_id, self.processor._conn
                )
                if user:
                    old_balance = user.balance
                    user.balance = round(
                        max(0.0, user.balance - self.processor.balance_discount), 2
                    )
                    await self.processor._model_service.users.update(
                        self.processor._conn,
                        user,
                        search_data={"tg_id": user.tg_id},
                    )
                    logger.info(
                        "Скидка за счёт баланса списана",
                        payment_id=payment_id,
                        tg_id=self.processor.tg_id,
                        discount=self.processor.balance_discount,
                        old_balance=old_balance,
                        new_balance=user.balance,
                    )
            except Exception as e:
                logger.warning(
                    "Ошибка при списании скидки за счёт баланса",
                    payment_id=payment_id,
                    tg_id=self.processor.tg_id,
                    discount=self.processor.balance_discount,
                    error=str(e),
                    exc_info=True,
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
