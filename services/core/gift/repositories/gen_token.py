from core.utils import generate_unique_token
from services.core.data.service import ServiceDataModel


class TokenGen:
    """Генерирует уникальный токен, проверяя его в кэше."""

    def __init__(self, model_data: ServiceDataModel):
        self.gift_data = model_data.gifts

    async def create(self) -> str:
        while True:
            token = generate_unique_token()
            if not await self.gift_data.get_by(token=token):
                return token
