import asyncio
import copy
from typing import Awaitable, Callable, Optional

import asyncpg

from client import XUISession, XUIAPIError
from models import Tariff, Server, Key
from services.core.data.service import ServiceDataModel

from services.core.keys.utils.reset import KeyResetter
from services.core.keys.utils.updating import KeyUpdater
from services.system.maintenance import PanelMaintenanceError, maintenance_mode

_renewal_locks: dict[str, asyncio.Lock] = {}


def _renewal_lock(email: str) -> asyncio.Lock:
    """Лок продления одного ключа в пределах процесса (приложение запускается одним воркером)."""
    return _renewal_locks.setdefault(email, asyncio.Lock())


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
        expiry_guard: Optional[Callable[[int], Awaitable[int]]] = None,
    ):
        """Продлевает ключ и сбрасывает флаги уведомлений.

        Работает как для активного, так и для просроченного ключа: панель
        сама отключает доступ клиента по expiryTime, но не удаляет его —
        ``ExpiryCalculator.key_duration`` отсчитывает новый срок от
        ``max(now, expiry)``, а ``extend_client_key`` всегда выставляет
        ``enable=True``, так что просроченный ключ корректно реактивируется.

        ``expiry_guard`` (платёжный путь): получает вычисленный срок до вызова панели и
        возвращает срок, который надо применить. Так повтор продления использует уже
        зафиксированный срок, а не считает новый от текущего expiry — иначе период
        добавился бы второй раз.
        """
        if await maintenance_mode.is_enabled(conn):
            raise PanelMaintenanceError(
                "Панель 3x-ui на профилактике — продление ключей временно недоступно"
            )

        # Два продления одного ключа не должны идти параллельно: иначе оба считают срок от
        # одного и того же снимка и второй период теряется. Лок на email покрывает весь путь
        # от чтения до записи в БД/кеш. Процессы не покрывает: scripts/sweep_pending_payments.py
        # в отдельном процессе этим локом не защищён.
        async with _renewal_lock(key.email):
            # Перечитываем ключ уже под замком: переданный снимок мог устареть, пока ждали
            # предыдущее продление того же ключа.
            current = await self.key_data.get_data(key.email, conn) or key
            # Работаем с копией: refresh_key мутирует объект, а current может быть объектом из кеша.
            # Если guard или панель упадут, кеш остаётся со старым expiry, и повтор не добавит период дважды.
            renewed = copy.copy(current)
            self.refresh.refresh_key(renewed, tariff, server, number_of_months)
            if expiry_guard is not None:
                renewed.expiry_time = await expiry_guard(renewed.expiry_time)
            # extend_client_key возвращает False, а не бросает исключение, при недоступной панели.
            # Без этой проверки БД и платёж считались бы успешными, а панель держала бы старый срок.
            if not await self.xui_session.extend_client_key(renewed):
                raise XUIAPIError(f"Не удалось продлить ключ {renewed.email} в панели 3x-ui")
            await self.key_data.update(conn, renewed, search_data={"email": renewed.email})

            if self.resetter:
                await self.resetter.reset_key_after_renewal(conn, renewed)
            return renewed
