"""Статистика приглашений одного пользователя для кабинета (вкладка «Друзья»)."""
from dataclasses import dataclass

import asyncpg

from config import REFERRAL_BONUS_PERCENT, REFERRAL_DISCOUNT_PERCENT

# Приглашённые — пользователи с referral_id = реферер (так же их видит
# ReferralBonusService); «оплатили» — у кого бонус уже начислен (check_referral).
_STATS_SQL = """
    SELECT
        (SELECT count(*) FROM users WHERE referral_id = $1 AND tg_id <> $1) AS invited,
        (SELECT count(*) FROM users WHERE referral_id = $1 AND tg_id <> $1 AND check_referral) AS paid,
        (SELECT coalesce(sum(reward_value), 0) FROM referral_rewards WHERE referrer_tg_id = $1) AS earned,
        (SELECT coalesce(balance, 0) FROM users WHERE tg_id = $1) AS balance
"""


@dataclass(frozen=True)
class ReferralStats:
    invited_count: int
    paid_count: int
    earned_total: float
    balance: float
    # Условия программы, чтобы клиенты не держали проценты у себя.
    bonus_percent: int
    friend_discount_percent: int


class ReferralStatsService:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def for_user(self, tg_id: int) -> ReferralStats:
        row = await self._pool.fetchrow(_STATS_SQL, tg_id)
        return ReferralStats(
            invited_count=row["invited"],
            paid_count=row["paid"],
            earned_total=round(float(row["earned"]), 2),
            balance=round(float(row["balance"] or 0), 2),
            bonus_percent=round(REFERRAL_BONUS_PERCENT * 100),
            friend_discount_percent=round(REFERRAL_DISCOUNT_PERCENT * 100),
        )
