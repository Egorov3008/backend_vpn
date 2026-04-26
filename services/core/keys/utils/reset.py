import asyncpg

from models import Key
from logger import logger
from services.cache.service import CacheService


class KeyResetter:
    """
    Сбрасывает флаги и параметры ключа после продления.
    
    Принцип единой ответственности:
    - Сбрасывает флаги notified_24h, notified_10h в FALSE
    - Сбрасывает used_traffic в 0.0
    - Обновляет кеш после успешного UPDATE в БД
    """

    def __init__(self, cache_service: CacheService):
        """
        Инициализация KeyResetter.
        
        Args:
            cache_service: Сервис кэширования для обновления кеша после сброса
        """
        self.cache_service = cache_service

    async def reset_key_after_renewal(
        self,
        conn: asyncpg.Pool,
        key: Key,
    ) -> bool:
        """
        Сбрасывает флаги уведомлений и счётчик трафика после продления ключа.
        
        Выполняет:
        1. UPDATE в БД: notified_24h = FALSE, notified_10h = FALSE, used_traffic = 0.0
        2. Обновление локального объекта key
        3. Обновление кеша с новыми значениями

        Args:
            conn: Подключение к базе данных
            key: Объект ключа

        Returns:
            True если сброс успешно выполнен, False если ключ не найден
        """
        try:
            # Шаг 1: Обновляем данные в БД
            result = await conn.execute(
                """
                UPDATE keys
                SET notified_24h = FALSE,
                    notified_10h = FALSE,
                    used_traffic = 0.0
                WHERE email = $1
                """,
                key.email,
            )

            # Извлекаем количество затронутых строк из результата
            # asyncpg возвращает строку вида "UPDATE N" или int/bool в тестах
            rows_affected = 0
            if isinstance(result, str) and result:
                # Формат "UPDATE N"
                rows_affected = int(result.split()[-1])
            elif isinstance(result, int):
                # Прямое число (в тестах)
                rows_affected = result
            elif result is True:
                # Булево True (в тестах)
                rows_affected = 1
            elif result is False:
                rows_affected = 0

            if rows_affected == 0:
                logger.warning(
                    "[KeyResetter] Ключ не найден для сброса после продления",
                    email=key.email,
                )
                return False

            # Шаг 2: Обновляем объект ключа локально
            key.notified_24h = False
            key.notified_10h = False
            key.used_traffic = 0.0

            # Шаг 3: Обновляем кеш с новыми значениями
            await self._update_cache(key)

            logger.info(
                "[KeyResetter] Ключ сброшен после продления (БД + кеш)",
                email=key.email,
                rows_affected=rows_affected,
            )
            return True

        except Exception as e:
            logger.error(
                "[KeyResetter] Ошибка при сбросе ключа после продления",
                email=key.email,
                error_type=type(e).__name__,
                error_message=str(e),
                exc_info=True,
            )
            raise

    async def _update_cache(self, key: Key) -> None:
        """
        Обновляет кеш с новыми значениями ключа после сброса.
        
        Args:
            key: Объект ключа с обновлёнными значениями
        """
        cache_key = f"key_{key.email}"
        await self.cache_service.keys.set(cache_key, key)
        logger.debug(
            "[KeyResetter] Кеш обновлён",
            email=key.email,
            cache_key=cache_key,
        )
