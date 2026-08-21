import asyncpg
from aiogram.exceptions import TelegramAPIError
from decimal import Decimal
from typing import TYPE_CHECKING

from config import REFERRAL_BONUS_PERCENT
from logger import logger
from services.cache.key_manager import CacheKeyManager

if TYPE_CHECKING:
    from services.cache.service import CacheService
    from services.core.data.service import ServiceDataModel


class ReferralBonusService:
    """Начисление реферальных бонусов при оплате.

    BUG-4/9 fix: ранее метод выполнял несколько отдельных INSERT/UPDATE
    без транзакции — это приводило к race condition (TOCTOU на check_referral)
    и потере бонуса при падении между шагами.

    Теперь атомарность обеспечивается через:
      1) сырой SQL `UPDATE users SET check_referral = TRUE WHERE tg_id = $1
         AND check_referral = FALSE RETURNING referral_id` — получаем referrer_id
         только если ещё не было начисления;
      2) если строка вернулась — внутри одной транзакции INSERT в
         referral_rewards и UPDATE users.balance у реферера;
      3) уведомление в Telegram отправляется только после commit.

    Награда: реферер получает 10% от платежа приглашённого на баланс.
    """

    def __init__(
        self,
        model_data: "ServiceDataModel",
        cache: "CacheService | None" = None,
    ):
        self._users = model_data.users
        self._data_service = model_data.data_service
        self._cache = cache

    async def _notify_referrer(self, referrer_tg_id: int, reward_value: float) -> None:
        """Отправить уведомление рефереру о начисленном бонусе."""
        from bot_project import bot

        text = (
            "🎉 <b>Реферальный бонус!</b>\n\n"
            f"Ваш приглашённый друг оплатил подписку.\n"
            f"Вам начислен бонус: <b>{reward_value:.2f} ₽</b>\n\n"
            "Спасибо, что приглашаете друзей! 👥"
        )
        try:
            await bot.send_message(
                chat_id=referrer_tg_id,
                text=text,
                parse_mode="HTML",
            )
        except TelegramAPIError as e:
            logger.warning(
                "Не удалось отправить уведомление рефереру",
                referrer_tg_id=referrer_tg_id,
                error=str(e),
            )

    async def process_referral_bonus(
        self, conn, referred_tg_id: int, payment_amount: float
    ) -> bool:
        """Начислить бонус пригласившему при первой оплате реферала.

        Args:
            conn: либо asyncpg.Connection, либо asyncpg.Pool.
                  Принимаем оба типа для совместимости с вызывающим кодом;
                  при Pool — берём соединение через acquire().
            referred_tg_id: tg_id оплатившего пользователя (реферала)
            payment_amount: Сумма платежа

        Returns:
            True, если бонус был начислен, False иначе
        """
        if not payment_amount or payment_amount <= 0:
            logger.warning("Реферальный бонус: некорректная сумма", amount=payment_amount)
            return False

        bonus_percent = float(REFERRAL_BONUS_PERCENT)
        reward_value = round(float(payment_amount) * bonus_percent, 2)
        if reward_value <= 0:
            return False

        # Поддерживаем как Connection, так и Pool (legacy-сигнатура)
        if isinstance(conn, asyncpg.Pool):
            async with conn.acquire() as connection:
                return await self._do_atomic_bonus(connection, referred_tg_id, reward_value)
        else:
            return await self._do_atomic_bonus(conn, referred_tg_id, reward_value)

    async def _do_atomic_bonus(
        self, conn: asyncpg.Connection, referred_tg_id: int, reward_value: float
    ) -> bool:
        """Атомарное начисление бонуса внутри одной транзакции.

        Returns:
            True, если бонус начислен, False иначе
        """
        # Шаг 1: атомарно помечаем referred.check_referral = TRUE и получаем
        # referral_id только если он ещё не был обработан.
        # WHERE-условие на check_referral = FALSE — защита от TOCTOU.
        mark_query = """
            UPDATE users
            SET check_referral = TRUE
            WHERE tg_id = $1
              AND check_referral = FALSE
              AND referral_id IS NOT NULL
              AND referral_id != tg_id
            RETURNING referral_id
        """
        try:
            row = await conn.fetchrow(mark_query, referred_tg_id)
        except Exception as exc:
            logger.error(
                "Реферальный бонус: не удалось пометить check_referral",
                tg_id=referred_tg_id,
                error=str(exc),
            )
            return False

        if not row:
            # Либо пользователь не найден, либо уже обработан, либо нет referrer,
            # либо self-referral (defense-in-depth). Различим по диагностике:
            user_row = await conn.fetchrow(
                "SELECT referral_id, check_referral FROM users WHERE tg_id = $1",
                referred_tg_id,
            )
            if not user_row:
                logger.debug("Реферальный бонус: пользователь не найден", tg_id=referred_tg_id)
            elif user_row["referral_id"] is None:
                logger.debug("Реферальный бонус: у пользователя нет реферера", tg_id=referred_tg_id)
            elif user_row["check_referral"]:
                logger.debug("Реферальный бонус уже начислен", tg_id=referred_tg_id)
            else:
                logger.warning("Реферальный бонус: неожиданный путь", tg_id=referred_tg_id)
            return False

        referrer_tg_id = row["referral_id"]

        # check_referral выставлен сырым UPDATE выше — синхронизируем кеш
        # реферала сразу (независимо от исхода шага 2), иначе он продолжит
        # получать реферальную скидку на всех последующих платежах из
        # устаревшего закешированного User (check_referral=False).
        if self._cache is not None:
            referred = await self._users.service.get(conn, tg_id=referred_tg_id)
            if referred:
                await self._cache.users.set(
                    CacheKeyManager.user(referred_tg_id), referred
                )

        # Шаг 2: одна транзакция для INSERT reward + UPDATE referrer.balance.
        # Если что-то упадёт — check_referral уже выставлен, но это безопасно:
        # повторный вызов завершится на row = None (step 1) и не продублирует бонус.
        # Зато денег не потеряем: либо вся транзакция, либо ничего.
        try:
            async with conn.transaction():
                # Страховка от self-referral на уровне данных (на случай бага в register)
                referrer_check = await conn.fetchval(
                    "SELECT 1 FROM users WHERE tg_id = $1 AND tg_id != $2",
                    referrer_tg_id, referred_tg_id,
                )
                if not referrer_check:
                    logger.error(
                        "Реферальный бонус: referrer_tg_id совпадает с referred_tg_id",
                        referrer_tg_id=referrer_tg_id,
                        referred_tg_id=referred_tg_id,
                    )
                    return False

                await conn.execute(
                    """
                    INSERT INTO referral_rewards
                        (referrer_tg_id, reward_type, reward_value, awarded_at)
                    VALUES ($1, 'discount', $2, NOW())
                    """,
                    referrer_tg_id, Decimal(str(reward_value)),
                )

                await conn.execute(
                    """
                    UPDATE users
                    SET balance = ROUND((balance + $1)::numeric, 2)
                    WHERE tg_id = $2
                    """,
                    reward_value, referrer_tg_id,
                )
        except Exception as exc:
            logger.error(
                "Реферальный бонус: ошибка транзакции (check_referral уже выставлен, "
                "повторный вызов будет no-op)",
                referrer_tg_id=referrer_tg_id,
                referred_tg_id=referred_tg_id,
                error=str(exc),
                exc_info=True,
            )
            return False

        logger.info(
            "Реферальный бонус начислен",
            referrer_tg_id=referrer_tg_id,
            referred_tg_id=referred_tg_id,
            reward_value=reward_value,
        )

        # Синхронизируем кэш реферера: balance выше обновлён сырым UPDATE в
        # обход self._users.update(), поэтому кеш сам не инвалидируется — до
        # следующего cache-sync (каждые 3ч) calculate_payment продолжал бы
        # отдавать balance_discount=0 из устаревшего закешированного User.
        # Читаем строку напрямую из БД (get_data вернул бы тот же устаревший
        # кеш), чтобы не полагаться на то, был ли объект уже закеширован.
        if self._cache is not None:
            referrer = await self._users.service.get(conn, tg_id=referrer_tg_id)
            if referrer:
                await self._cache.users.set(
                    CacheKeyManager.user(referrer_tg_id), referrer
                )

        # Уведомление — после commit, чтобы не слать фантомных сообщений.
        await self._notify_referrer(referrer_tg_id, reward_value)
        return True
