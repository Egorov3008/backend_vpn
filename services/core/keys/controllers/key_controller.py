from typing import Dict
from logger import logger

from services.core.data.service import ServiceDataModel
from services.core.keys.models.key_model import KeyModel
from services.core.keys.view.key_view import KeyView


class KeyController:
    """Контроллер — управляет логикой получения и отображения данных ключа."""

    def __init__(self, service_data: ServiceDataModel):
        self.user_service = service_data.users

    async def getter_key_data(self, email: str) -> Dict:
        """Основной метод для aiogram_dialog — возвращает данные окна ключа"""
        try:
            key_data = await self.user_service.get_data(email)
            if not key_data:
                return KeyView.render_error("❌ Ключ не найден")

            model = KeyModel(key_data)
            return KeyView.render_success(model)

        except Exception as e:
            logger.error(
                "Ошибка при получении данных ключа",
                email=email,
                error_type=type(e).__name__,
                error_message=str(e)
            )
            return KeyView.render_error(f"❌ Ошибка: {str(e)}")
