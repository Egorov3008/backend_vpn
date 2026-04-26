from typing import Optional

from models import GiftLink
from services.core.data.service import ServiceDataModel


class CheckerGiftLink:
    """Проверяет использование подарка"""

    def __init__(self, model_data: ServiceDataModel):
        self._gift_data = model_data.gifts

    async def check(self, user_id: int) -> bool:
        gift_link: Optional[GiftLink] = await self._gift_data.get_data(user_id)
        if not gift_link:
            return False
        return gift_link.is_redeemable()
