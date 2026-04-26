from typing import Optional

from models import Key, Tariff, Server
from services.core.keys.utils.calculator import ExpiryCalculator


class KeyUpdater:
    """Обновляет данные ключа"""

    def __init__(self, expiry_calculator: ExpiryCalculator):
        self.expiry_calculator = expiry_calculator

    def refresh_key(
        self,
        key: Key,
        tariff: Tariff,
        server: Optional[Server],
        number_of_months: int = 1,
    ):
        """Устанавливает параметры тарифа для ключа"""
        new_expiry_timestamp = self.expiry_calculator.key_duration(
            key, tariff.period, number_of_months
        )

        key.tariff_id = tariff.id
        key.total_gb = int(tariff.traffic_limit * (2**30) * number_of_months)
        key.limit_ip = tariff.limit_ip
        key.name_tariff = tariff.name_tariff
        key.expiry_time = new_expiry_timestamp
        key.server_info = server
        key.used_traffic = 0.0

        return key
