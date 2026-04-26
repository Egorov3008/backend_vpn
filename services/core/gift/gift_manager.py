from datetime import datetime

import asyncpg

from logger import logger
from models import GiftLink
from services.core.data.service import ServiceDataModel
from services.core.gift.repositories.gen_token import TokenGen


class GiftLinkProvider:
    """Сервис для управления подарками."""

    def __init__(self, gen_token: TokenGen, model_data: ServiceDataModel):
        self._gen_token = gen_token
        self._gift_data = model_data.gifts

    async def get_gift_link(
        self, user_id: int, conn: asyncpg.Pool, tariff_id: int = 7
    ) -> GiftLink | None:
        """
        Возвращает существующий GiftLink по sender_tg_id или создаёт новый.
        Синхронизирует БД и кэш.
        """
        gift = await self._gift_data.get_data(user_id)
        if gift:
            logger.debug(
                "Найдена закэшированная ссылка на подарок", sender_tg_id=user_id
            )
            return gift

        # Генерируем уникальный токен
        token = await self._gen_token.create()

        # Создаём объект GiftLink
        gift = GiftLink(
            sender_tg_id=user_id,
            tariff_id=tariff_id,
            token=token,
            created_at=datetime.now(),
        )

        await self._gift_data.save_data(conn, gift, sender_tg_id=user_id)
        return gift

    async def application(
        self, conn: asyncpg.Pool, gift: GiftLink, recipient_tg_id: int, email: str
    ):
        """Подтверждает подарок."""
        gift.redeem(recipient_tg_id, email)
        await self._gift_data.update(conn, gift, {"sender_tg_id": gift.sender_tg_id})
