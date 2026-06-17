from typing import Optional

from config import DEFAULT_PRICING_PLAN
from logger import logger
from models import Tariff
from services.core.data.service import ServiceDataModel
from client import PanelClient


class TariffMatcher:
    """Определяет тариф клиента по параметрам, сравнивая с тарифами из кеша."""

    SPECIAL_INBOUND_TARIFF = {6: 1}

    def __init__(self, model_data: ServiceDataModel) -> None:
        self.model_data = model_data

    async def match(self, client: PanelClient) -> int:
        """Определяет tariff_id для клиента XUI.

        Все ключи безлимитные (tariff.traffic_limit=0), поэтому exact match
        идёт только по limit_ip; правило SPECIAL_INBOUND_TARIFF — по inbound_id.
        """
        # 1. Специальное правило для inbound_id
        if client.inbound_id in self.SPECIAL_INBOUND_TARIFF:
            return self.SPECIAL_INBOUND_TARIFF[client.inbound_id]

        # 2. Точное совпадение по limit_ip
        tariffs = await self.model_data.tariffs.get_all()
        exact = self._find_exact_match(tariffs, client)
        if exact:
            return exact.id

        # 3. Совпадение только по limit_ip (первый найденный)
        by_ip = self._find_by_limit_ip(tariffs, client)
        if by_ip:
            logger.warning(
                "Тариф определен только по limit_ip",
                email=client.email,
                limit_ip=client.limit_ip,
                tariff_id=by_ip.id,
            )
            return by_ip.id

        # 4. Fallback
        logger.warning(
            "Тариф не найден, используется DEFAULT_PRICING_PLAN",
            email=client.email,
            limit_ip=client.limit_ip,
        )
        return int(DEFAULT_PRICING_PLAN)

    def _find_exact_match(
        self, tariffs: list[Tariff], client: PanelClient
    ) -> Optional[Tariff]:
        """Точное совпадение по limit_ip (трафик безлимитный, не различаем)."""
        return self._find_by_limit_ip(tariffs, client)

    def _find_by_limit_ip(
        self, tariffs: list[Tariff], client: PanelClient
    ) -> Optional[Tariff]:
        """Совпадение по limit_ip (первый найденный)."""
        for tariff in tariffs:
            if tariff.limit_ip == client.limit_ip:
                return tariff
        return None
