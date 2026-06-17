from datetime import datetime
from typing import Optional

from logger import logger
from models import Key, Tariff, Server
from services.core.keys.utils.calculator import ExpiryCalculator, TRIAL_TARIFF_ID


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
        old_expiry_time = key.expiry_time
        new_expiry_timestamp = self.expiry_calculator.key_duration(
            key, tariff.period, number_of_months
        )

        key.tariff_id = tariff.id
        key.limit_ip = tariff.limit_ip
        key.name_tariff = tariff.name_tariff
        key.expiry_time = new_expiry_timestamp
        key.server_info = server
        key.used_traffic = 0.0

        added_ms = new_expiry_timestamp - int(datetime.now().timestamp() * 1000)
        logger.info(
            "[KeyUpdater] Ключ обновлён по тарифу",
            email=key.email,
            old_tariff_id=key.tariff_id,
            new_tariff_id=tariff.id,
            is_trial_renewal=key.tariff_id == TRIAL_TARIFF_ID,
            tariff_period=tariff.period,
            number_of_months=number_of_months,
            old_expiry_time=old_expiry_time,
            new_expiry_time=new_expiry_timestamp,
            added_days=round(added_ms / 86_400_000, 4),
        )

        return key
