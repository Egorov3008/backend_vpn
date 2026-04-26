from typing import Optional

import asyncpg

from logger import logger
from models import User
from services.core.data.service import ServiceDataModel


class TrialService:
    """Процессинг с пробным периодом"""

    def __init__(self, model_data: ServiceDataModel):
        self.user_data = model_data.users

    async def installation_trial(
        self, user_id, conn: asyncpg.Pool, trial: int = 1
    ) -> User:
        """Агрегатор пробного периода"""
        user: Optional[User] = await self.user_data.get_data(user_id)
        logger.debug("Полученные данные о пользователе", user=user)
        if not user:
            raise AttributeError("Пользователь не найден")
        user.trial = trial
        await self.user_data.update(conn, user, {"tg_id": user_id})
        logger.info(
            "Пробный период успешно установлен",
            user_id=user_id,
            trial=trial,
        )
        return user
