from database import DataService
from models import User, Key, Server, Inbound, PaymentModel, GiftLink, Tariff, ReferralLink
from models.stocks.stock import Stock
from services.cache.service import CacheService
from services.core.data.base import BaseData
from services.core.data.protocols import DataProtocol


class ServiceDataModel:
    """✅ Использует CacheService согласно Cache Access Rules.

    BaseData получает весь cache_service и доступит к нужным моделям через _get_cache_model().
    """

    def __init__(self, cache_service: CacheService, data_service: DataService) -> None:
        self.cache_service = cache_service
        self.data_service = data_service
        self.users: DataProtocol[User] = BaseData[User](
            User, self.cache_service, self.data_service.users
        )
        self.keys: DataProtocol[Key] = BaseData[Key](
            Key, self.cache_service, self.data_service.keys
        )
        self.servers: DataProtocol[Server] = BaseData[Server](
            Server, self.cache_service, self.data_service.servers
        )
        self.inbounds: DataProtocol[Inbound] = BaseData[Inbound](
            Inbound, self.cache_service, self.data_service.inbounds
        )
        self.payments: DataProtocol[PaymentModel] = BaseData[PaymentModel](
            PaymentModel, self.cache_service, self.data_service.payments
        )
        self.gifts: DataProtocol[GiftLink] = BaseData[GiftLink](
            GiftLink, self.cache_service, self.data_service.gifts
        )
        self.tariffs: DataProtocol[Tariff] = BaseData[Tariff](
            Tariff, self.cache_service, self.data_service.tariffs
        )
        self.stocks: DataProtocol[Stock] = BaseData[Stock](
            Stock, self.cache_service, self.data_service.stocks
        )
        self.referral_links: DataProtocol[ReferralLink] = BaseData[ReferralLink](
            ReferralLink, self.cache_service, self.data_service.referral_links
        )
