import traceback
from datetime import datetime
from typing import Optional

import asyncpg

from client import XUISession
from logger import logger
from models import Tariff, Key
from services.core.data.service import ServiceDataModel
from services.core.keys.utils.calculator import ExpiryCalculator
from services.core.keys.utils.formtion import FormationKey
from services.metrics.registry import key_created_total, key_creation_errors_total


class CreateKey:
    """
    Сервис для создания новых ключей в системе.

    Реализует интерфейс KeyCreator и использует композицию для работы
    с различными зависимостями.
    """

    def __init__(
        self,
        model_data: ServiceDataModel,
        xui_session: XUISession,
        expiry: ExpiryCalculator,
        formation: FormationKey,
    ):
        """
        Инициализирует сервис создания ключей.

        """

        self.key_data = model_data.keys
        self.xui_session = xui_session
        self.form = formation
        self.expiry = expiry

    async def proces(
        self,
        tg_id: int,
        tariff: Tariff,
        server_id: int,
        conn: asyncpg.Pool,
        number_of_months: int = 1,
        inbound_id_override: Optional[int] = None,
    ) -> Optional[dict]:
        """
        Создает новый ключ для пользователя в системе.

        Args:
            inbound_id_override: форсирует конкретный inbound (для лендинга
                и других специальных потоков). None = стандартное поведение
                (берётся первый из server.inbound_ids).
        """

        try:
            key: Optional[Key] = await self.form.form_new_key(
                tg_id, tariff, server_id, number_of_months, inbound_id_override
            )
            # Проверка успешного создания ключа
            if not key:
                logger.error(
                    "Не удалось сформировать ключ", tg_id=tg_id, tariff_id=tariff.id
                )
                return None

            add_result = await self.xui_session.add_client(
                client_id=key.client_id,
                email=key.email,
                tg_id=key.tg_id,
                limit_ip=key.limit_ip,
                inbound_ids=key.inbound_ids or [key.inbound_id],
                expiry_time=key.expiry_time,
            )

            # add_client возвращает False при провале панели (success:false,
            # circuit breaker open, сетевая ошибка). НЕ сохраняем фантомный ключ,
            # которого нет в панели — иначе юзер получит «ключ создан», а в панели
            # клиента не будет (баг dp5649).
            if add_result is False:
                logger.error(
                    "Ключ не создан в панели — сохранение в БД отменено",
                    tg_id=tg_id,
                    email=key.email,
                    tariff_id=tariff.id,
                )
                return None

            # Сохранение данных ключа
            await self.key_data.save_data(conn, key, email=key.email)

            days = self._get_days(key.expiry_time)

            # Формирование ссылки подключения
            # key.key уже содержит полный URL подписки (subscription_url/email)
            link_to_connect = key.key

            # Определяем тип ключа: trial или paid
            key_type = "trial" if tariff.amount == 0 else "paid"
            key_created_total.labels(type=key_type).inc()

            return {
                "public_link": key.key,
                "days": days,
                "link_to_connect": link_to_connect,
                "email": key.email,
            }

        except Exception as e:
            key_creation_errors_total.labels(error_type=type(e).__name__).inc()
            logger.error(
                "Ошибка при создании ключа",
                error_msg=str(e),
                traceback=traceback.format_exc(),
            )
            return None

    def _get_days(self, expiry_time: int):
        """Получает количество дней до истечения срока действия ключа."""
        expiry_time = datetime.fromtimestamp(expiry_time / 1000)
        remaining_time = expiry_time - datetime.utcnow()
        return remaining_time.days
