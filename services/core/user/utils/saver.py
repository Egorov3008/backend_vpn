import asyncpg

from models import User
from services.core.data.service import ServiceDataModel


class SeverUser:
    """Сохранение пользователя"""

    def __init__(self, model_data: ServiceDataModel):
        self.user_data = model_data.users

    async def register_user(self, conn: asyncpg.Pool, **kwargs) -> User:

        if "tg_id" not in kwargs or "server_id" not in kwargs:
            raise ValueError("Не указан tg_id или server_id")
        new_user = User.from_dict(kwargs)
        await self.user_data.save_data(conn, new_user, tg_id=new_user.tg_id)
        return new_user
