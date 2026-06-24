import asyncio

import asyncpg

from database.service import DataService
from logger import logger
from services.cache.factory import LoaderFactory
from services.cache.key_manager import CacheKeyManager
from services.cache.service import CacheService
from services.cache.storage import CacheStorage


class LoadingService(LoaderFactory):
    """Загрузка данных в кеш с единой системой ключей"""

    def __init__(
        self, cache: CacheService, data_service: DataService, pool: asyncpg.Pool = None
    ):
        super().__init__(pool)
        self.cache = cache
        self.user_srv = data_service.users
        self.key_srv = data_service.keys
        self.tariff_srv = data_service.tariffs
        self.gifts = data_service.gifts
        self.server_srv = data_service.servers
        self.payments = data_service.payments
        self.referral_links = data_service.referral_links
        self.stock_srv = data_service.stocks
        self.keys = CacheKeyManager()

    async def loading(self):
        """Загрузить все данные в кеш с единообразными ключами"""
        await self.load_all(
            (
                self.server_srv,
                lambda s: self.cache.servers.set(self.keys.server(s.id), s),
            ),
            (self.user_srv, lambda u: self.cache.users.set(self.keys.user(u.tg_id), u)),
            (self.key_srv, lambda k: self.cache.keys.set(self.keys.key(k.email), k)),
            (
                self.tariff_srv,
                lambda t: self.cache.tariffs.set(self.keys.tariff(t.id), t),
            ),
            (self.gifts, lambda g: self.cache.gifts.set(self.keys.gift(g.id), g)),
            (
                self.payments,
                lambda p: self.cache.payments.set(self.keys.payment(p.payment_id), p),
            ),
            (
                self.referral_links,
                lambda r: self.cache.referral_links.set(
                    self.keys.referral_link(r.token), r
                ),
            ),
            (
                self.stock_srv,
                lambda s: self.cache.stocks.set(self.keys.stock(s.tg_id), s),
            ),
        )
        logger.info(
            "Загрузка кэша завершена (ключи приведены в соответствие с CacheKeyManager)"
        )

    async def load_server(self):
        """Загрузка серверов"""
        pool = await self.ensure_pool()
        async with pool.acquire() as conn:
            server_db = await self.server_srv.get_all(conn)
            for server in server_db:
                await self.cache.servers.set(self.keys.server(server.id), server)


async def main():
    storage = CacheStorage()
    loader = LoadingService(cache=CacheService(storage), data_service=DataService())
    await loader.loading()


if __name__ == "__main__":
    asyncio.run(main())
