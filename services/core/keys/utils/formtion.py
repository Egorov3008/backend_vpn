import uuid
from typing import Optional

from core.utils import generate_random_email
from logger import logger
from models import Tariff, Key
from services.cache.service import CacheService
from services.core.connect_module.repositories.form_data import FormConnectionData
from services.core.keys.utils.calculator import ExpiryCalculator
from services.core.keys.utils.inbounds import paid_inbound_ids, is_subscription


class FormationKey:
    """Формирует ключ"""

    def __init__(
        self,
        cache: CacheService,
        connected_data: FormConnectionData,
        expiry: ExpiryCalculator,
    ):

        self.cache = cache
        self.connected_data = connected_data
        self.expiry = expiry

    async def form_new_key(
        self,
        tg_id: int,
        tariff: Tariff,
        server_id: int,
        number_of_months: int = 1,
        inbound_id_override: Optional[int] = None,
    ) -> Optional[Key]:
        """Формирует новый объект ключа.

        Args:
            inbound_id_override: если задан, используется как единственный inbound
                (для лендинга и других специальных потоков). Иначе берётся
                первый из server.inbound_ids.
        """
        email = await self._generate_email()

        # Защита от дрейфа данных: все платные тарифы должны быть 30-дневными.
        # Если в БД оказался period != 30, логируем warning и приводим к 30,
        # чтобы ключи не создавались на 37/45/... дней.
        tariff_period = tariff.period
        if tariff.id != 10 and tariff_period != 30:
            logger.warning(
                "Некорректный period платного тарифа, приводим к 30 дням",
                tariff_id=tariff.id,
                name_tariff=tariff.name_tariff,
                original_period=tariff_period,
            )
            tariff_period = 30

        new_expiry_time = self.expiry.key_duration_new_key(
            tariff_period, number_of_months
        )
        client_id = self._generate_client_id()
        server_data = await self.connected_data.data(user_id=tg_id, server_id=server_id)

        if not server_data:
            logger.error(
                "Не удалось получить данные сервера для создания ключа",
                tg_id=tg_id,
                server_id=server_id,
            )
            return None

        subscription_url = f"{server_data.get('subscription_url')}/{email}"

        if inbound_id_override is not None:
            # Landing-page flow: форсируем конкретный inbound.
            inbound_ids = [inbound_id_override]
            primary_inbound_id = inbound_id_override
        else:
            inbound_ids = paid_inbound_ids() if is_subscription(tariff) else (
                server_data.get("inbound_ids", []))
            primary_inbound_id = inbound_ids[0] if inbound_ids else 0

        key = Key(
            tg_id=tg_id,
            email=email,
            client_id=client_id,
            limit_ip=tariff.limit_ip,
            expiry_time=int(new_expiry_time),
            inbound_id=int(primary_inbound_id),
            inbound_ids=inbound_ids,
            key=subscription_url,
            tariff_id=tariff.id,
        )
        return key

    async def _generate_email(self):
        """Генератор имени ключа"""
        while True:
            email = generate_random_email()
            keys = await self.cache.keys.all()
            existing_key = any(k.email for k in keys if k.email == email)
            if not existing_key:
                return email

    def _generate_client_id(self) -> Optional[str]:
        """Генерирует client_id"""
        return str(uuid.uuid4())
