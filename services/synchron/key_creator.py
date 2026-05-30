# services/synchron/key_creator.py

from typing import Optional

from asyncpg import Pool
from logger import logger

from client import PanelClient
from models import Key
from services.core.data.service import ServiceDataModel
from services.synchron.tariff_matcher import TariffMatcher


class KeyCreator:
    """
    Сервис для создания и настройки ключей на основе данных из XUI.
    Отвечает за определение тарифа, создание пользователя (при необходимости)
    и формирование объекта Key с последующим сохранением в БД и кэш.
    """

    def __init__(
        self, model_data: ServiceDataModel, pool: Pool, tariff_matcher: TariffMatcher
    ) -> None:
        self.model_data = model_data
        self.pool = pool
        self.tariff_matcher = tariff_matcher

    async def ensure_user_exists(self, tg_id: int) -> bool:
        """
        Создаёт пользователя в системе, если он не существует.

        Args:
            tg_id: Telegram ID пользователя

        Returns:
            True, если пользователь существует или был создан
        """
        try:
            from models import User

            user = await self.model_data.users.get_data(tg_id)
            if not user:
                user_obj = User(tg_id=tg_id, server_id=2)
                await self.model_data.users.save_data(
                    self.pool, user_obj, tg_id=user_obj.tg_id
                )
                logger.info("Создан новый пользователь", tg_id=tg_id)
            return True
        except Exception as e:
            logger.error("Ошибка создания пользователя", tg_id=tg_id, error=str(e))
            return False

    async def create_key(
        self, client: PanelClient, used_traffic: int = 0
    ) -> Optional[Key]:
        """
        Создаёт новый объект Key на основе клиента XUI и сохраняет его.

        Args:
            client: Объект клиента из XUI (PanelClient)
            used_traffic: Объём использованного трафика

        Returns:
            Объект Key или None при ошибке
        """
        try:
            server = await self.model_data.servers.get_data(2)
            if not server:
                logger.error("Сервер не найден", server_id=2)
                return None

            tariff_id = await self.tariff_matcher.match(client)

            link = (
                f"{server.subscription_url}/{client.email}"
                if client.email == client.sub_id
                else f"{server.subscription_url}/{client.sub_id}"
            )

            key = Key(
                email=client.email,
                client_id=client.id,
                limit_ip=client.limit_ip,
                total_gb=client.total_gb,
                used_traffic=used_traffic,
                inbound_id=client.inbound_id,
                expiry_time=client.expiry_time,
                key=link,
                tg_id=client.tg_id,
                tariff_id=tariff_id,
            )

            await self.model_data.keys.save_data(self.pool, key, email=key.email)
            logger.info(
                "Ключ создан и сохранён", email=client.email, tg_id=client.tg_id
            )
            return key

        except Exception as e:
            logger.error(
                "Ошибка при создании ключа", email=client.email, error=str(e)
            )
            return None
