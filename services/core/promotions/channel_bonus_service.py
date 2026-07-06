"""Сервис промо-бонуса «+N дней за подписку на Telegram-канал».

Бизнес-логика живёт в backend. Bot только проверяет подписку через
Telegram API и вызывает этот endpoint.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import asyncpg

from client import XUISession
from config import GRACE_PERIOD_DAYS, settings
from logger import logger
from models import Key, User
from services.cache.key_manager import CacheKeyManager
from services.cache.service import CacheService
from services.core.data.service import ServiceDataModel
from services.core.keys.utils.inbounds import GRACE_PERIOD_MS, paid_inbound_ids
from services.core.keys.utils.reset import KeyResetter
from services.core.keys.utils.status import KeyStatus


PROMO_ID = "channel_subscription_bonus"


def _ms(days: int) -> int:
    return days * 24 * 60 * 60 * 1000


@dataclass
class ChannelBonusResult:
    status: str
    email: Optional[str] = None
    new_expiry_time: Optional[int] = None
    new_expiry_date: Optional[str] = None
    keys: Optional[List[dict]] = None


class ChannelBonusService:
    """Начисляет +CHANNEL_BONUS_DAYS дней за подписку на канал."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        service_data: ServiceDataModel,
        cache: CacheService,
        xui: XUISession,
    ):
        self.pool = pool
        self.service_data = service_data
        self.cache = cache
        self.xui = xui
        self.bonus_days = getattr(settings, "channel_bonus_days", 7)
        self.resetter = KeyResetter(cache_service=cache)

    async def claim(self, tg_id: int, email: Optional[str] = None) -> ChannelBonusResult:
        """Атомарно начисляет бонус или возвращает статус выбора/отказа.

        Флаг ``user_promo_claims`` фиксируется ONLY когда ключ реально
        продлён на +bonus_days. Пути ``choose_key`` и ``no_active_keys``
        флаг не ставят — иначе повторный вызов с выбранным email попадал
        в ``already_claimed`` без фактического начисления бонуса
        (баг для пользователей с несколькими ключами).
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # 1. Уже получал бонус?
                already = await conn.fetchval(
                    "SELECT 1 FROM user_promo_claims WHERE tg_id = $1 AND promo_id = $2",
                    tg_id,
                    PROMO_ID,
                )
                if already:
                    logger.info("Канальный бонус уже получен", tg_id=tg_id)
                    return ChannelBonusResult(status="already_claimed")

                # 2. Находим активные/grace ключи пользователя
                keys = await self._load_user_keys(conn, tg_id)
                eligible = [k for k in keys if KeyStatus.of(k) in (KeyStatus.ACTIVE, KeyStatus.GRACE)]
                if not eligible:
                    # Нет активных ключей — флаг не ставим, пользователь
                    # сможет получить бонус позже, когда создаст ключ.
                    logger.info("Нет активных ключей для канального бонуса", tg_id=tg_id)
                    return ChannelBonusResult(status="no_active_keys")

                # 3. Если ключей несколько и email не указан — просим выбрать.
                #    Флаг НЕ ставим: бонус ещё не начислен.
                if email is None and len(eligible) > 1:
                    return ChannelBonusResult(
                        status="choose_key",
                        keys=[self._key_info(k) for k in eligible],
                    )

                # 4. Определяем целевой ключ
                target = self._find_target(eligible, email)
                if target is None:
                    return ChannelBonusResult(
                        status="choose_key",
                        keys=[self._key_info(k) for k in eligible],
                    )

                # 5. Атомарно фиксируем флаг "бонус получен".
                #    ON CONFLICT защищает от гонки двух одновременных вызовов:
                #    проигравший получит already_claimed без продления.
                row = await conn.fetchrow(
                    """
                    INSERT INTO user_promo_claims (tg_id, promo_id)
                    VALUES ($1, $2)
                    ON CONFLICT (tg_id, promo_id) DO NOTHING
                    RETURNING id
                    """,
                    tg_id,
                    PROMO_ID,
                )
                if not row:
                    logger.info("Канальный бонус уже получен (гонка)", tg_id=tg_id)
                    return ChannelBonusResult(status="already_claimed")

                # 6. Продлеваем ключ. При ошибке транзакция откатит флаг,
                #    и пользователь сможет повторить попытку.
                return await self._extend_key(conn, target)

    async def _load_user_keys(self, conn: asyncpg.Connection, tg_id: int) -> List[Key]:
        """Загружает ключи пользователя напрямую из БД (не кеш)."""
        rows = await conn.fetch(
            """
            SELECT
                tg_id, client_id, email, created_at, expiry_time, key,
                inbound_id, notified_10h, notified_24h, tariff_id,
                limit_ip, notified_expired_grace, converted_tg_id,
                landing_uid, grace_expiry
            FROM keys
            WHERE tg_id = $1
            """,
            tg_id,
        )
        keys = []
        for r in rows:
            keys.append(Key(**dict(r)))
        return keys

    def _find_target(self, keys: List[Key], email: Optional[str]) -> Optional[Key]:
        if email:
            for k in keys:
                if k.email == email:
                    return k
            return None
        # Если один ключ — берём его
        if len(keys) == 1:
            return keys[0]
        return None

    def _key_info(self, key: Key) -> dict:
        return {
            "email": key.email,
            "expiry_time": key.expiry_time,
            "expiry_date": self._format_expiry(key.expiry_time),
            "status": KeyStatus.of(key),
        }

    @staticmethod
    def _format_expiry(expiry_time: int) -> str:
        dt = datetime.fromtimestamp(expiry_time / 1000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M")

    async def _extend_key(self, conn: asyncpg.Connection, key: Key) -> ChannelBonusResult:
        """Продлевает ключ на bonus_days с корректным grace_expiry."""
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        bonus_ms = _ms(self.bonus_days)

        new_expiry = max(key.expiry_time, now_ms) + bonus_ms
        new_grace = new_expiry + GRACE_PERIOD_MS

        status = KeyStatus.of(key)

        # Для grace-ключа восстанавливаем paid overlay
        if status == KeyStatus.GRACE:
            if not await self.xui.set_inbounds(key.email, paid_inbound_ids()):
                logger.error(
                    "ChannelBonusService: не удалось восстановить paid overlay",
                    email=key.email,
                )
                raise RuntimeError("Не удалось восстановить paid overlay для grace-ключа")

        # Панель всегда хранит grace_expiry как expiryTime
        saved_expiry = key.expiry_time
        key.expiry_time = new_grace
        key.grace_expiry = new_grace
        panel_ok = await self.xui.extend_client_key(key)
        key.expiry_time = saved_expiry

        if not panel_ok:
            logger.error("ChannelBonusService: не удалось продлить ключ в панели", email=key.email)
            raise RuntimeError("Не удалось продлить ключ в 3x-UI панели")

        # Обновляем БД
        await conn.execute(
            """
            UPDATE keys
            SET expiry_time = $1,
                grace_expiry = $2,
                notified_10h = FALSE,
                notified_24h = FALSE
            WHERE email = $3
            """,
            new_expiry,
            new_grace,
            key.email,
        )

        # Обновляем объект и кеш
        key.expiry_time = new_expiry
        key.grace_expiry = new_grace
        key.notified_10h = False
        key.notified_24h = False
        await self.cache.keys.set(CacheKeyManager.key(key.email), key)

        logger.info(
            "Канальный бонус начислен",
            tg_id=key.tg_id,
            email=key.email,
            bonus_days=self.bonus_days,
            old_expiry=saved_expiry,
            new_expiry=new_expiry,
            new_grace=new_grace,
        )

        return ChannelBonusResult(
            status="claimed",
            email=key.email,
            new_expiry_time=new_expiry,
            new_expiry_date=self._format_expiry(new_expiry),
        )
