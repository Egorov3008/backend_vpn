from datetime import datetime
from typing import Dict, Any, Optional

from logger import logger
from models import Key
from services.core.data.service import ServiceDataModel


class KeyService:
    def __init__(self, modul_data: ServiceDataModel):
        self.key_data = modul_data.keys
        self.server_data = modul_data.servers
        self.user_data = modul_data.users

    async def getter_key_data(self, email: str) -> Dict[str, Any]:
        """Данные для формирования окна ключа"""
        try:
            # Получаем детали ключа
            key_details: Optional[Key] = await self.key_data.get_data(email)
            if not key_details:
                return {"error": True, "error_message": "❌ Ключ не найден"}

            # Преобразуем timestamp в datetime
            expiry_time = key_details.expiry_time
            expiry_date = datetime.utcfromtimestamp(expiry_time / 1000)
            current_date = datetime.utcnow()
            time_left = expiry_date - current_date

            # Определяем статус подписки
            if time_left.total_seconds() <= 0:
                status_emoji = "🔴"
                status_text = "Истекла"
                time_left_message = "Осталось часов: 0"
            elif time_left.days > 0:
                status_emoji = "🟢"
                status_text = "Активна"
                time_left_message = f"Осталось дней: {time_left.days}"
            else:
                status_emoji = "🟡"
                status_text = "Заканчивается"
                hours_left = time_left.seconds // 3600
                time_left_message = f"Осталось часов: {hours_left}"

            # Форматируем данные
            formatted_expiry_date = expiry_date.strftime("%d %B %Y года")
            used_traffic = round(key_details.used_traffic / (1024**3), 2)

            logger.debug(
                "Параметры",
                used_traffic=used_traffic,
            )
            return {
                "error": False,
                "keys": key_details.key,
                "tariff_name": key_details.name_tariff or "Не указан",
                "used_traffic": used_traffic,
                "expiry_date": formatted_expiry_date,
                "status_emoji": status_emoji,
                "status_text": status_text,
                "time_left_message": time_left_message,
                "is_trial": key_details.tariff_id == 10,
                "not_trial_tariff": key_details.tariff_id != 10,
                "is_active": time_left.total_seconds() > 0,
                "days_left": max(0, time_left.days),
                "hours_left": max(0, time_left.seconds // 3600),
            }

        except Exception as e:
            logger.error(
                "Ошибка при получении деталей ключа",
                error_type=type(e).__name__,
                error_message=str(e)
            )
            return {
                "error": True,
                "error_message": f"❌ Ошибка при загрузке данных: {str(e)}",
            }
