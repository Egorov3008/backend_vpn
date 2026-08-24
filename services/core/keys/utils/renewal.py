from typing import Optional

import asyncpg

from client import XUISession
from models import Tariff, Server, Key
from services.core.data.service import ServiceDataModel

from services.core.keys.utils.reset import KeyResetter
from services.core.keys.utils.updating import KeyUpdater
from services.system.maintenance import PanelMaintenanceError, maintenance_mode


class KeyRenewal:
    """Продление ключа."""

    def __init__(
        self,
        model_data: ServiceDataModel,
        xui_session: XUISession,
        refresh_key: KeyUpdater,
        resetter: Optional[KeyResetter] = None,
    ):
        self.key_data = model_data.keys
        self.xui_session = xui_session
        self.refresh = refresh_key
        self.resetter = resetter

    async def extension_key(
        self,
        key: Key,
        conn: asyncpg.Pool,
        server: Server,
        tariff: Tariff,
        number_of_months: Optional[int] = 1,
    ):
        """Продлевает ключ и сбрасывает флаги уведомлений.

        Работает как для активного, так и для просроченного ключа: панель
        сама отключает доступ клиента по expiryTime, но не удаляет его —
        ``ExpiryCalculator.key_duration`` отсчитывает новый срок от
        ``max(now, expiry)``, а ``extend_client_key`` всегда выставляет
        ``enable=True``, так что просроченный ключ корректно реактивируется.
        """
        if await maintenance_mode.is_enabled(conn):
            raise PanelMaintenanceError(
                "Панель 3x-ui на профилактике — продление ключей временно недоступно"
            )

        refresh_key = self.refresh.refresh_key(key, tariff, server, number_of_months)
        await self.xui_session.extend_client_key(refresh_key)
        await self.key_data.update(conn, key, search_data={"email": key.email})

        if self.resetter:
            await self.resetter.reset_key_after_renewal(conn, key)
        return refresh_key
