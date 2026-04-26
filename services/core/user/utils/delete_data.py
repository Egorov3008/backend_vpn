import asyncpg

from client import XUISession
from logger import logger
from services.core.data.service import ServiceDataModel


class DeleteUser:
    def __init__(
        self, model_data: ServiceDataModel, xui_session: XUISession, pool: asyncpg.Pool
    ):

        self.user_data = model_data.users
        self.key_data = model_data.keys
        self.xui_session = xui_session
        self.pool = pool

    async def delete(self, user_id: int) -> None:
        """Удаление пользователя и всех его ключей (с панели, из БД и кеша)."""
        keys_result = await self.key_data.get_by(tg_id=user_id)
        # Нормализуем результат: может быть Key, список Key'ей или None
        if keys_result is None:
            keys = []
        elif isinstance(keys_result, list):
            keys = [k for k in keys_result if k is not None]
        else:
            keys = [keys_result]

        user = await self.user_data.get_data(user_id)
        for key in keys:
            # Удаляем ключ с сервера 3x-ui панели
            deleted = await self.xui_session.delete_client(
                email=key.email,
                inbound_id=key.inbound_id,
                client_id=key.client_id,
            )
            if not deleted:
                logger.warning(
                    "Не удалось удалить ключ с панели 3x-ui",
                    email=key.email,
                    user_id=user_id,
                )
            await self.key_data.delete_data(self.pool, key)
        if user:
            await self.user_data.delete_data(self.pool, user)
