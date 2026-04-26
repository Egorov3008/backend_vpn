from typing import List

from models import Tariff
from services.core.data.service import ServiceDataModel
from services.core.user.utils.checked_admin import CheckedUser
from config import AVAILABLE_RATES_LIST


class TariffData:
    """Класс для отображения тарифов."""

    def __init__(self, model_data: ServiceDataModel, checked_user: CheckedUser):
        self.tariff_data = model_data.tariffs
        self.checked_user = checked_user

    async def get_tariffs(self) -> List[Tariff]:
        """Возвращает список тарифов."""
        tariffs = await self.tariff_data.get_data()
        return [tariff for tariff in tariffs if tariff.id in AVAILABLE_RATES_LIST]

    async def get(self, user_id: int) -> List[Tariff]:
        tariffs = await self.tariff_data.get_all()
        if not self.checked_user.check(user_id):
            tariffs = [
                tariff for tariff in tariffs if tariff.id in AVAILABLE_RATES_LIST
            ]
        if not tariffs:
            raise AttributeError("Тарифы не найдены")

        return tariffs
