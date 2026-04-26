"""
Модули для хранения моделей данных приложения.
"""

from .users.user import User
from .keys.key import Key
from .servers.server import Server
from .servers.inbound import Inbound
from .tariffs.tariff import Tariff
from .payments.payment import PaymentModel
from .referrals.referral import Referral
from .referrals.referral_link import ReferralLink
from .referrals.referral_redemption import ReferralRedemption
from .referrals.referral_reward import ReferralReward
from .gifts.gift_link import GiftLink

# GiftRedemption удален, его функциональность интегрирована в GiftLink
from .cache import CacheItem, REGISTRATE_USER
from .mass_mailing import MassMailing
