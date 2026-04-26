import asyncpg
from aiogram.exceptions import TelegramAPIError

from config import REFERRAL_BONUS_PERCENTAGES
from logger import logger
from models import ReferralReward
from services.core.data.service import ServiceDataModel


class ReferralBonusService:
    """Начисление реферальных бонусов при оплате."""

    def __init__(self, model_data: ServiceDataModel):
        self._users = model_data.users
        self._data_service = model_data.data_service

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
        self, conn: asyncpg.Pool, referred_tg_id: int, payment_amount: float
    ) -> None:
        """Начислить бонус пригласившему при первой оплате реферала.

        Args:
            conn: Подключение к БД
            referred_tg_id: tg_id оплатившего пользователя (реферала)
            payment_amount: Сумма платежа
        """
        user = await self._users.get_data(referred_tg_id)
        if not user:
            logger.debug("Реферальный бонус: пользователь не найден", tg_id=referred_tg_id)
            return

        # Бонус начисляется только один раз
        if user.referral_id is None:
            return
        if user.check_referral:
            logger.debug("Реферальный бонус уже начислен", tg_id=referred_tg_id)
            return

        if not payment_amount or payment_amount <= 0:
            logger.warning("Реферальный бонус: некорректная сумма", amount=payment_amount)
            return

        # Вычисляем бонус (уровень 1)
        bonus_percent = float(REFERRAL_BONUS_PERCENTAGES.get("1", "0.10"))
        reward_value = round(float(payment_amount) * bonus_percent, 2)

        if reward_value <= 0:
            return

        # Создаём награду для пригласившего
        reward = ReferralReward(
            referrer_tg_id=user.referral_id,
            reward_type="discount",
            reward_value=str(reward_value),
        )
        await self._data_service.referral_rewards.create(conn, **reward.to_dict())

        # Начисляем баланс рефереру
        referrer = await self._users.get_data(user.referral_id)
        if referrer:
            referrer.balance = round(referrer.balance + reward_value, 2)
            await self._users.update(conn, referrer, search_data={"tg_id": referrer.tg_id})

        # Помечаем что бонус уже начислен
        user.check_referral = True
        await self._users.update(
            conn, user, search_data={"tg_id": user.tg_id}
        )

        logger.info(
            "Реферальный бонус начислен",
            referrer_tg_id=user.referral_id,
            referred_tg_id=referred_tg_id,
            reward_value=reward_value,
            payment_amount=payment_amount,
        )

        # Уведомляем реферера о начисленном бонусе
        await self._notify_referrer(user.referral_id, reward_value)
