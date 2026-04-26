import asyncpg
from aiogram.exceptions import TelegramForbiddenError

from models import User
from services.core.user.utils.data import UserData


class BlockUserChecker:
    def __init__(self, user_data: UserData, pool: asyncpg.Pool):
        self.error_block = TelegramForbiddenError
        self.user_data = user_data
        self._pool = pool

    async def check(self, error: Exception, user: User):
        """Проверка на блокировку пользователя"""
        if not isinstance(error, self.error_block):
            return
        user.is_blocked = True
        user.created_at = user.created_at
        await self.user_data.update(self._pool, user)
