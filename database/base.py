from typing import TypeVar, Generic, Optional, List, Dict, Any

from asyncpg import Pool
import asyncpg

from config import DATABASE_URL
from logger import logger

T = TypeVar("T")  # Обобщенный тип для моделей


async def create_db_pool() -> Pool:
    """Создает пул соединений с базой данных.

    Returns:
        asyncpg.pool.Pool: Пул соединений к базе данных
    """
    logger.debug("Инициализация пула соединений с БД")
    try:
        pool = await asyncpg.create_pool(DATABASE_URL)
        logger.success("Пул соединений с БД успешно создан")
        return pool
    except Exception as e:
        logger.error(
            "Ошибка создания пула соединений с БД",
            error_type=type(e).__name__,
            error_message=str(e)
        )
        raise


class _ConnectionWrapper:
    """Обёртка для Connection, чтобы использовать его как async context manager."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *args):
        pass


class BaseRepository(Generic[T]):
    """Базовый класс для всех репозиториев.

    Этот класс предоставляет основные операции для работы с таблицами в базе данных,
    включая получение, создание, обновление и удаление записей.
    """

    def __init__(self, table_name: str, model: type):
        self.table_name = table_name
        self.model = model

    async def _acquire(self, pool_or_conn):
        """Получить соединение: если передан Pool — acquire, если Connection — использовать напрямую."""
        if isinstance(pool_or_conn, asyncpg.Pool):
            return pool_or_conn.acquire()
        # Connection — возвращаем no-op контекстный менеджер
        return _ConnectionWrapper(pool_or_conn)

    async def get(self, pool: asyncpg.Pool, **kwargs) -> Optional[T]:
        """Получение записи по ID."""
        if len(kwargs) != 1:
            raise ValueError("Only one filter parameter is allowed")
        key, value = next(iter(kwargs.items()))
        async with await self._acquire(pool) as conn:
            record = await conn.fetchrow(
                f"SELECT * FROM {self.table_name} WHERE {key} = $1", value
            )
            return self.model(**record) if record else None

    async def get_all(self, pool: asyncpg.Pool) -> List[T]:
        """Получение всех записей."""
        async with await self._acquire(pool) as conn:
            records = await conn.fetch(f"SELECT * FROM {self.table_name}")
            return [self.model(**r) for r in records]

    async def filter(self, pool: asyncpg.Pool, order_by: Optional[str] = None, **kwargs) -> List[T]:
        """Получение нескольких записей по фильтру."""
        if len(kwargs) != 1:
            raise ValueError("Only one filter parameter is allowed")
        key, value = next(iter(kwargs.items()))
        order_clause = f" ORDER BY {order_by}" if order_by else ""
        async with await self._acquire(pool) as conn:
            records = await conn.fetch(
                f"SELECT * FROM {self.table_name} WHERE {key} = $1{order_clause}", value
            )
            return [self.model(**r) for r in records]

    async def delete(self, pool: asyncpg.Pool, **kwargs) -> bool:
        """Удаление записи по ID."""
        if not kwargs:
            return False
        key, value = next(iter(kwargs.items()))
        async with await self._acquire(pool) as conn:
            result = await conn.execute(
                f"DELETE FROM {self.table_name} WHERE {key} = $1", value
            )
            return "DELETE 1" in result

    async def create(self, pool: asyncpg.Pool, **kwargs) -> bool:
        """Добавление новой записи.

        Args:
            pool (asyncpg.Pool | asyncpg.Connection): Пул или соединение с БД.
            **kwargs: Данные для новой записи.
        """
        columns = ", ".join(kwargs.keys())
        placeholders = ", ".join([f"${i + 1}" for i in range(len(kwargs))])
        query = f"""
            INSERT INTO {self.table_name} ({columns})
            VALUES ({placeholders})
            RETURNING 1
        """
        async with await self._acquire(pool) as conn:
            result = await conn.execute(query, *kwargs.values())
            return "INSERT 1" == result

    async def update(
        self, pool: asyncpg.Pool, search_data: Dict[str, Any], **kwargs
    ) -> bool:
        """
        Обновляет запись по условиям из search_data.
        Поля из search_data автоматически исключаются из обновления (чтобы не SET-ить ключ).
        
        Использует ON CONFLICT DO UPDATE для обработки гонок.
        """
        if not kwargs or not search_data:
            return False
        # Исключаем поля, используемые в WHERE, из обновления
        filtered_kwargs = {k: v for k, v in kwargs.items() if k not in search_data}
        if not filtered_kwargs:
            return False
        where_key, where_value = next(iter(search_data.items()))

        # Защита от SQL-инъекций: проверка where_key
        allowed_fields = getattr(self, "allowed_fields", {where_key})
        if where_key not in allowed_fields:
            raise ValueError(f"Unsafe field in WHERE clause: {where_key}")

        set_clause = ", ".join(
            [f"{k} = ${i + 2}" for i, k in enumerate(filtered_kwargs.keys())]
        )
        
        # Определяем primary key для ON CONFLICT
        # Для таблицы keys это (tg_id, client_id)
        if self.table_name == "keys":
            # Используем UPSERT для обработки гонки
            set_clause_with_excluded = ", ".join(
                [f"{k} = EXCLUDED.{k}" for k in filtered_kwargs.keys()]
            )
            query = f"""
                INSERT INTO {self.table_name} ({", ".join(filtered_kwargs.keys())}, {where_key})
                VALUES ({", ".join([f"${i + 2}" for i in range(len(filtered_kwargs))])}, $1)
                ON CONFLICT (tg_id, client_id) DO UPDATE SET {set_clause_with_excluded}
            """
            async with await self._acquire(pool) as conn:
                result = await conn.execute(query, where_value, *filtered_kwargs.values())
                return result == "INSERT 0 1" or result == "UPDATE 1"
        else:
            query = f"""
                UPDATE {self.table_name}
                SET {set_clause}
                WHERE {where_key} = $1
            """
            async with await self._acquire(pool) as conn:
                result = await conn.execute(query, where_value, *filtered_kwargs.values())
                return result == "UPDATE 1"
