from typing import Optional, Dict, Any

from asyncpg import Pool

from client import XUISession
from config import settings
from logger import logger

from services.cache.service import CacheService
from services.core.keys.utils.calculator import ExpiryCalculator
from services.core.keys.utils.create_key import CreateKey
from services.core.keys.utils.landing_upgrade import upgrade_landing_key
from services.core.payment.processor import PaymentProcessor
from services.core.notifications.protocols import INotifier


class KeyCreationService:
    """
    Сервис создания ключа после оплаты.

    Использует INotifier для отправки уведомлений — не зависит от aiogram.
    Это позволяет тестировать сервис с моком notifier без инициализации бота.
    """

    def __init__(
        self,
        processor: PaymentProcessor,
        create_key: CreateKey,
        notifier: Optional[INotifier] = None,
        xui_session: Optional[XUISession] = None,
        cache: Optional[CacheService] = None,
        pool: Optional[Pool] = None,
    ):
        self.processor = processor
        self.create_key = create_key
        self.notifier = notifier
        # landing-upgrade flow (см. _find_landing_origin_key/process ниже).
        self.xui_session = xui_session
        self.cache = cache
        self.pool = pool
        self.expiry = ExpiryCalculator()

    async def _find_landing_origin_key(self, tg_id: int):
        """Найти landing-ключ юзера, готовый к апгрейду:
        landing_uid set, converted_tg_id == tg_id, ещё не апгрейжен
        (владелец всё ещё псевдо-tg_id: апгрейд переносит tg_id на converted_tg_id)."""
        try:
            keys = await self.processor._model_service.keys.get_all()
        except Exception:
            return None
        for k in keys or []:
            if (getattr(k, "landing_uid", None)
                    and getattr(k, "converted_tg_id", None) == tg_id
                    and getattr(k, "tg_id", None) != tg_id):
                return k
        return None

    async def process(
        self, tariff_id: str = None, payment_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Создаёт ключ и отправляет уведомление пользователю.

        Args:
            tariff_id: ID тарифа (опционально, извлекается из payment_type если не указан)
            payment_id: платёж, за который выдаётся ключ. Если по нему ключ уже есть
                (retry после сбоя), новый не создаётся, а возвращаются данные существующего
                с флагом already_created=True.

        Returns:
            key_data: Данные созданного ключа или None при ошибке
        """
        try:
            if tariff_id is None:
                operation, tariff_id = self.processor.extract_operation()
                if operation != "create_key":
                    raise ValueError(
                        f"Ожидалась операция 'create_key', получено: {operation}"
                    )

            # Предохранитель от двойной выдачи: ключ по этому платежу уже существует.
            # Проверка стоит до landing-апгрейда, иначе повтор после частичного апгрейда
            # создал бы ещё один ключ.
            if payment_id:
                existing = await self.processor._model_service.keys.service.get(
                    self.processor._conn, payment_id=payment_id
                )
                if existing is not None:
                    logger.warning(
                        "[Цена:CreateKey] Ключ по этому платежу уже существует — новый не создаём",
                        tg_id=self.processor.tg_id,
                        payment_id=payment_id,
                        email=existing.email,
                    )
                    return {
                        "public_link": existing.key,
                        "days": self.create_key._get_days(existing.expiry_time),
                        "link_to_connect": existing.key,
                        "email": existing.email,
                        "already_created": True,
                    }

            tariff = await self.processor._model_service.tariffs.get_data(
                int(tariff_id), self.processor._conn
            )
            logger.info(
                "[Цена:CreateKey] Создание ключа после оплаты",
                tg_id=self.processor.tg_id,
                tariff_id=tariff_id,
                tariff_amount=tariff.amount if tariff else None,
                number_of_months=self.processor.number_of_months,
                paid_amount=self.processor.amount,
            )

            # Landing-upgrade: если у юзера есть неапгрейженный landing-ключ —
            # апгрейдим тот же клиент (Happ-URL сохраняется), не создаём новый.
            if self.xui_session is not None and self.cache is not None and self.pool is not None:
                landing_key = await self._find_landing_origin_key(self.processor.tg_id)
                if landing_key is not None:
                    new_expiry = self.expiry.key_duration_new_key(
                        tariff.period, self.processor.number_of_months
                    )
                    upgraded = await upgrade_landing_key(
                        xui_session=self.xui_session,
                        model_data=self.processor._model_service,
                        cache=self.cache,
                        pool=self.pool,
                        key=landing_key,
                        tariff=tariff,
                        new_expiry=new_expiry,
                        transfer_tg=True,
                        payment_id=payment_id,
                    )
                    if upgraded is not None:
                        logger.info(
                            "[Цена:CreateKey] Landing-ключ апгрейдирован",
                            tg_id=self.processor.tg_id,
                            email=upgraded.email,
                        )
                        return {
                            "public_link": upgraded.key,
                            "days": 0,
                            "link_to_connect": upgraded.key,
                            "email": upgraded.email,
                        }
                    logger.warning(
                        "[Цена:CreateKey] upgrade_landing_key провален, создаём новый ключ",
                        tg_id=self.processor.tg_id,
                    )

            key_data = await self.create_key.proces(
                tg_id=self.processor.tg_id,
                tariff=tariff,
                server_id=settings.xui_server_id,
                conn=self.processor._conn,
                number_of_months=self.processor.number_of_months,
                payment_id=payment_id,
            )

            if not key_data:
                raise ValueError("Не удалось создать ключ")

            logger.info(
                "[Цена:CreateKey] Ключ успешно создан",
                tg_id=self.processor.tg_id,
                tariff_id=tariff_id,
            )

            return key_data

        except Exception as e:
            logger.error(
                "Ошибка при создании ключа",
                error_type=type(e).__name__,
                error_message=str(e),
                tg_id=self.processor.tg_id,
                exc_info=True,
            )
            raise

    async def send_notification(self, key_data: Dict[str, Any]) -> None:
        """
        Отправить уведомление о созданном ключе.

        Выносится отдельно чтобы позволить вызывающему коду решить,
        нужно ли отправлять уведомление и когда.

        Args:
            key_data: Данные ключа от create_key.proces()
        """
        if self.notifier is None:
            logger.debug(
                "Notifier не настроен, пропускаем отправку уведомления",
                tg_id=self.processor.tg_id,
            )
            return

        try:
            await self.notifier.send_key_created(
                tg_id=self.processor.tg_id,
                key_data=key_data,
            )
        except Exception as e:
            logger.warning(
                "Не удалось отправить уведомление о созданном ключе",
                tg_id=self.processor.tg_id,
                error=str(e),
            )
