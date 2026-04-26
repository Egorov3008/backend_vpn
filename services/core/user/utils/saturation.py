import asyncio
from typing import Dict, Any, List

from services.core.data.service import ServiceDataModel


class SaturationUser:
    """Класс для работы с данными пользователя"""

    def __init__(self, model_data: ServiceDataModel):
        self.server = model_data.servers
        self.user_data = model_data.users

    async def refresh(self, tg_id: int) -> Dict[str, Any]:
        """Обновляет данные пользователя"""
        user = await self.user_data.get_data(tg_id)
        if not user:
            return {}
        server = await self.server.get_data(user.server_id)
        keys = await self.user_data.get_by(tg_id=tg_id)

        return {"user": user, "connect_module": server, "keys": keys}

    async def get_data_for_users(self) -> List[Dict[str, Any]]:
        """Возвращает данные для всех пользователей"""
        users = await self.user_data.get_all()

        tasks = [self.refresh(user.tg_id) for user in users]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_users_data = []
        for result in results:
            if isinstance(result, Exception):
                continue
            if result:  # Проверяем, что результат не пустой
                all_users_data.append(result)
        return all_users_data
