from typing import Any, Callable, Awaitable, Tuple

import asyncpg

from config import DATABASE_URL
from database.base import BaseRepository


class LoaderFactory:
    """Фабрика для загрузки данных из репозиториев."""

    def __init__(self, pool):
        self._pool = pool

    async def ensure_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(DATABASE_URL)
        return self._pool

    async def load(
        self, repository: BaseRepository, set_func: Callable[[Any], Awaitable[None]]
    ) -> None:
        """
        Загружает все объекты из репозитория и сохраняет их через set_func.
        """
        pool = await self.ensure_pool()
        items = await repository.get_all(pool)
        for item in items:
            await set_func(item)

    async def load_all(
        self, *sources: Tuple[Any, Callable[[Any], Awaitable[None]]]
    ) -> None:
        """
        Загружает данные из нескольких источников.
        Пример:
            await loader.load_all(
                (user_repo, cache.set_user),
                (key_repo, cache.set_key),
                (tariff_repo, cache.set_tariff),
            )
        """
        for repository, set_func in sources:
            await self.load(repository, set_func)
