"""Окно «только Telegram» после окончания подписки.

До 3x-ui v3.8.0 панель отдавала внешние ссылки клиента (externalLinks =
EXTERNAL_SUB_URL, конфиг для Telegram) и после его expiryTime. С v3.8.0
(MHSanaei/3x-ui#6333) внешние ссылки просроченного или выключенного клиента
в подписку не попадают, настройки, чтобы это вернуть, в панели нет.

Поэтому окно держит бэкенд. Когда срок оплаты (keys.expiry_time) прошёл:
1. клиент отвязывается от всех инбаундов (полный VPN выключен);
2. expiryTime клиента в панели сдвигается на expiry_time + TELEGRAM_GRACE_DAYS;
3. конец окна пишется в keys.telegram_grace_until.

Пока окно идёт, подписка клиента содержит только EXTERNAL_SUB_URL. Когда
окно кончится, панель сама перестанет её отдавать. keys.expiry_time не
меняется, поэтому продление считает срок от даты оплаты, а не от конца окна.
Продление возвращает инбаунды в XUISession.extend_client_key.
"""
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

import asyncpg

from client import XUISession
from config import settings
from logger import logger
from models import Key, Tariff
from services.core.data.service import ServiceDataModel
from services.core.keys.utils.inbounds import is_subscription
from services.core.keys.utils.renewal import _renewal_lock
from services.system.maintenance import maintenance_mode

_DAY_MS = 24 * 3600 * 1000


def _now_ms() -> int:
    return int(time.time() * 1000)


def grace_until(key: Key, grace_days: int) -> int:
    """Конец окна для ключа, мс."""
    return key.expiry_time + grace_days * _DAY_MS


def needs_grace(key: Key, tariff: Optional[Tariff], now_ms: int, grace_days: int) -> bool:
    """Нужно ли открыть окно для ключа сейчас (без обращения к панели)."""
    if grace_days <= 0 or not key.expiry_time or key.expiry_time <= 0:
        return False
    if key.telegram_grace_until:
        return False  # окно уже открыто
    if not (key.expiry_time <= now_ms < grace_until(key, grace_days)):
        return False
    # Анонимный ключ с лендинга (ещё не привязан к пользователю) — не подписка.
    if key.landing_uid and not key.converted_tg_id:
        return False
    return is_subscription(tariff)


@dataclass
class GraceRunResult:
    opened: int = 0
    failed: int = 0
    skipped: int = 0


class TelegramGrace:
    """Открывает окно «только Telegram» для ключей с истёкшей подпиской."""

    def __init__(
        self,
        model_data: ServiceDataModel,
        xui_session: XUISession,
        pool: asyncpg.Pool,
        grace_days: Optional[int] = None,
        external_subscription_url: Optional[str] = None,
        now_ms: Callable[[], int] = _now_ms,
    ):
        self.model_data = model_data
        self.xui = xui_session
        self.pool = pool
        self.grace_days = settings.telegram_grace_days if grace_days is None else grace_days
        self.external_url = (
            settings.external_subscription_url
            if external_subscription_url is None
            else external_subscription_url
        )
        self.now_ms = now_ms

    @property
    def enabled(self) -> bool:
        # Без EXTERNAL_SUB_URL в окне нечего отдавать: клиент остался бы совсем без ссылок.
        return self.grace_days > 0 and bool(self.external_url)

    async def run(self) -> GraceRunResult:
        result = GraceRunResult()
        if not self.enabled:
            return result
        if await maintenance_mode.is_enabled(self.pool):
            logger.info("Окно Telegram: панель на профилактике, прогон пропущен")
            return result
        keys: Iterable[Key] = await self.model_data.keys.get_all(self.pool)
        now = self.now_ms()
        for key in keys:
            tariff = None
            if key.tariff_id:
                tariff = await self.model_data.tariffs.get_data(key.tariff_id, self.pool)
            if not needs_grace(key, tariff, now, self.grace_days):
                result.skipped += 1
                continue
            opened = await self._open_locked(key, tariff)
            if opened is None:
                result.skipped += 1
            elif opened:
                result.opened += 1
            else:
                result.failed += 1
        if result.opened or result.failed:
            logger.info(
                "Окно Telegram: прогон завершён",
                opened=result.opened,
                failed=result.failed,
            )
        return result

    async def _open_locked(self, key: Key, tariff: Optional[Tariff]) -> Optional[bool]:
        """Открывает окно под локом продления. None — ключ успели продлить."""
        # Без лока продление между выборкой и open() вернуло бы инбаунды,
        # а open() по старому снимку тут же отвязал бы их у оплаченного ключа.
        async with _renewal_lock(key.email):
            current = await self.model_data.keys.get_data(key.email, self.pool) or key
            if not needs_grace(current, tariff, self.now_ms(), self.grace_days):
                return None
            return await self.open(current)

    async def open(self, key: Key) -> bool:
        """Переводит клиента в окно. Ошибка — False, следующий прогон повторит."""
        until = grace_until(key, self.grace_days)
        # Сначала отвязываем инбаунды: если сдвиг срока ниже упадёт, клиент
        # останется просто просроченным, а не получит полный VPN до конца окна.
        if not await self.xui.set_inbounds(key.email, []):
            logger.warning("Окно Telegram: не удалось отвязать инбаунды", email=key.email)
            return False
        try:
            await self.xui.update_standalone_client(key.email, expiryTime=until, enable=True)
        except Exception as e:
            logger.warning("Окно Telegram: не удалось сдвинуть срок в панели", email=key.email, error=str(e))
            return False
        # Ключи до коммита 5b696c4 могли остаться без внешней ссылки.
        await self.xui.set_external_subscription(key.email, self.external_url)

        key.telegram_grace_until = until
        await self.model_data.keys.update(self.pool, key, search_data={"email": key.email})
        logger.info("Окно Telegram открыто", email=key.email, until_ms=until)
        return True
