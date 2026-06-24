from database.base import BaseRepository
from database.protocols import DatabaseProtocol
from models import User, Key, Server, PaymentModel, Tariff, GiftLink, LoginCode
from models import ReferralLink, ReferralRedemption, ReferralReward
from models.stocks.stock import Stock


class DataService:
    """Основной сервис"""

    def __init__(self) -> None:
        self.users: DatabaseProtocol[User] = BaseRepository[User](
            table_name="users", model=User
        )
        self.keys: DatabaseProtocol[Key] = BaseRepository[Key](
            table_name="keys", model=Key
        )
        self.servers: DatabaseProtocol[Server] = BaseRepository[Server](
            table_name="servers", model=Server
        )
        self.payments: DatabaseProtocol[PaymentModel] = BaseRepository[PaymentModel](
            table_name="payments", model=PaymentModel
        )
        self.tariffs: DatabaseProtocol[Tariff] = BaseRepository[Tariff](
            table_name="tariff", model=Tariff
        )
        self.gifts: DatabaseProtocol[GiftLink] = BaseRepository[GiftLink](
            table_name="gift_links", model=GiftLink
        )
        # Новые методы, требуют рефакторинга БД
        self.stocks: DatabaseProtocol[Stock] = BaseRepository[Stock](
            table_name="stocks", model=Stock
        )
        self.referral_links: DatabaseProtocol[ReferralLink] = BaseRepository[ReferralLink](
            table_name="referral_links", model=ReferralLink
        )
        self.referral_redemptions: DatabaseProtocol[ReferralRedemption] = BaseRepository[ReferralRedemption](
            table_name="referral_redemptions", model=ReferralRedemption
        )
        self.referral_rewards: DatabaseProtocol[ReferralReward] = BaseRepository[ReferralReward](
            table_name="referral_rewards", model=ReferralReward
        )
        self.login_codes: DatabaseProtocol[LoginCode] = BaseRepository[LoginCode](
            table_name="login_codes", model=LoginCode
        )
