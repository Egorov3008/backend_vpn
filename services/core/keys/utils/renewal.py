from typing import Optional

import asyncpg

from client import XUISession
from models import Tariff, Server, Key
from services.core.data.service import ServiceDataModel

from services.core.keys.utils.inbounds import paid_inbound_ids, GRACE_PERIOD_MS
from services.core.keys.utils.reset import KeyResetter
from services.core.keys.utils.status import KeyStatus
from services.core.keys.utils.updating import KeyUpdater


class KeyRenewal:
    """Продление ключа."""

    def __init__(
        self,
        model_data: ServiceDataModel,
        xui_session: XUISession,
        refresh_key: KeyUpdater,
        resetter: Optional[KeyResetter] = None,
        grace_manager=None,
    ):
        self.key_data = model_data.keys
        self.xui_session = xui_session
        self.refresh = refresh_key
        self.resetter = resetter
        self.grace_manager = grace_manager

    async def extension_key(
        self,
        key: Key,
        conn: asyncpg.Pool,
        server: Server,
        tariff: Tariff,
        number_of_months: Optional[int] = 1,
    ):
        """Продлевает ключ и сбрасывает флаги уведомлений.

        Ветвление по статусу ключа:
          - GRACE   → делегирует в grace_manager.renew_from_grace;
          - EXPIRED → raises ValueError (нужен новый ключ);
          - ACTIVE  → обычное продление + reconciliation inbounds к платному
                      набору + panel expiryTime=grace_expiry.
        """
        status = KeyStatus.of(key)
        if status == KeyStatus.GRACE:
            if self.grace_manager is None:
                raise RuntimeError("grace_manager не настроен для продления из grace")
            renewed = await self.grace_manager.renew_from_grace(key, tariff, number_of_months)
            if renewed is None:
                raise ValueError("Не удалось продлить ключ из grace (панель провалена)")
            return renewed
        if status == KeyStatus.EXPIRED:
            raise ValueError(
                "Ключ истёк (grace закончился) — продление невозможно, нужен новый ключ"
            )

        # active path
        refresh_key = self.refresh.refresh_key(key, tariff, server, number_of_months)
        # reconcile inbounds to paid set (heal drift) and set panel expiryTime=grace_expiry
        if key.grace_expiry is not None:
            await self.xui_session.set_inbounds(key.email, paid_inbound_ids())
            panel_expiry = key.expiry_time + GRACE_PERIOD_MS
            key.grace_expiry = panel_expiry
            saved_expiry = key.expiry_time
            key.expiry_time = panel_expiry
            await self.xui_session.extend_client_key(key)
            key.expiry_time = saved_expiry
        else:
            await self.xui_session.extend_client_key(refresh_key)
        await self.key_data.update(conn, key, search_data={"email": key.email})

        if self.resetter:
            await self.resetter.reset_key_after_renewal(conn, key)
        return refresh_key