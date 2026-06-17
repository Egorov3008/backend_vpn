from typing import Dict
from logger import logger


class KeyView:
    """Отвечает за логирование и форматирование данных для UI."""

    @staticmethod
    def render_success(key_model) -> Dict:
        """Логируем и возвращаем данные модели"""
        logger.debug(
            "Статистика ключа",
            email=key_model.key.email,
            used=key_model.used_traffic_gb,
        )
        return key_model.to_dict()

    @staticmethod
    def render_error(message: str) -> Dict:
        return {"error": True, "error_message": message}
