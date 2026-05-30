from typing import List, Set, Tuple

from logger import logger

from models import User, Key
from client import PanelClient


class CacheComparator:
    """
    Сервис для сравнения данных с XUI-панели и кэша.
    Определяет отсутствующие ключи и пользователей.
    """

    def __init__(self) -> None:
        # Данные из панели
        self.keys_panel: List[str] = []
        self.users_panel: List[int] = []

        # Данные из кэша
        self.keys_cache: List[str] = []
        self.users_cache: List[int] = []

        # Результаты сравнения
        self.out_keys: List[str] = []
        self.out_users: List[int] = []

    def set_panel_data(self, clients: List[PanelClient]) -> None:
        """
        Устанавливает данные, полученные с панели (email, tg_id).

        Args:
            clients: Список клиентов с XUI
        """
        self.keys_panel = [c.email for c in clients if c.email]
        self.users_panel = [
            c.tg_id
            for c in clients
            if isinstance(c.tg_id, int) and c.tg_id > 0
        ]

        logger.info(
            "Данные с панели установлены",
            keys_count=len(self.keys_panel),
            users_count=len(self.users_panel),
        )

    async def set_cache_data(self, get_all_keys_func, get_all_users_func) -> None:
        """
        Асинхронно загружает данные из кэша.

        Args:
            get_all_keys_func: Асинх-функция, возвращающая List[Key]
            get_all_users_func: Асинх-функция, возвращающая List[User]
        """
        # Получаем ключи
        keys_cache: List[Key] = await get_all_keys_func()
        self.keys_cache = [key.email for key in keys_cache if key.email]

        # Получаем пользователей
        users_cache: List[User] = await get_all_users_func()
        self.users_cache = [user.tg_id for user in users_cache if user.tg_id]

        logger.debug(
            "Кэш загружен",
            keys_cached=len(self.keys_cache),
            users_cached=len(self.users_cache),
        )

    def compare(self) -> Tuple[List[str], List[int]]:
        """
        Сравнивает данные панели и кэша, находит различия.

        Returns:
            (отсутствующие_ключи, отсутствующие_пользователи)
        """
        keys_panel_set: Set[str] = set(self.keys_panel)
        keys_cache_set: Set[str] = set(self.keys_cache)
        users_panel_set: Set[int] = set(self.users_panel)
        users_cache_set: Set[int] = set(self.users_cache)

        self.out_keys = list(keys_panel_set - keys_cache_set)
        self.out_users = list(users_panel_set - users_cache_set)

        if self.out_keys:
            logger.info("Найдены отсутствующие ключи в кэше", count=len(self.out_keys))
        else:
            logger.debug("Все ключи из панели присутствуют в кэше")

        if self.out_users:
            logger.info(
                "Найдены отсутствующие пользователи в кэше", count=len(self.out_users)
            )
        else:
            logger.debug("Все пользователи из панели присутствуют в кэше")

        return self.out_keys, self.out_users

    def get_out_keys(self) -> List[str]:
        """Возвращает email ключей, отсутствующих в кэше."""
        return self.out_keys

    def get_out_users(self) -> List[int]:
        """Возвращает tg_id пользователей, отсутствующих в кэше."""
        return self.out_users
