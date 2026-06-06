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

    # Маппинг таблиц и их bigint-полей для явного приведения типов в запросах
    # Нужно потому что asyncpg по умолчанию использует int32 для параметров
    BIGINT_FIELDS: Dict[str, set] = {
        "referral_redemptions": {"referred_tg_id"},
        "referral_links": {"referrer_tg_id"},
        "referral_rewards": {"referrer_tg_id"},
        "users": {"tg_id", "referral_id"},
        "registrate_msg_user": {"tg_id"},
    }

    def __init__(self, table_name: str, model: type):
        self.table_name = table_name
        self.model = model

    @staticmethod
    def _known_fields(model: type) -> Optional[set]:
        """Return the set of dataclass field names for `model`, or None when unknown."""
        try:
            from dataclasses import fields as _dc_fields

            return {f.name for f in _dc_fields(model)}
        except (TypeError, AttributeError):
            return None

    def _adapt(self, record: Any) -> Dict[str, Any]:
        """Преобразует запись БД в dict, отбрасывая ключи, отсутствующие в модели.

        Защищает от дрейфа схемы (например, незакоммиченных колонок в БД),
        который раньше приводил к TypeError на старте приложения.
        """
        data = dict(record) if record is not None else {}
        if not isinstance(data, dict):
            return data
        known = self._known_fields(self.model)
        if known is None:
            return data
        return {k: v for k, v in data.items() if k in known}

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
        # Проверяем, является ли поле bigint
        bigint_fields = self.BIGINT_FIELDS.get(self.table_name, set())
        type_cast = "::bigint" if key in bigint_fields else ""
        async with await self._acquire(pool) as conn:
            record = await conn.fetchrow(
                f"SELECT * FROM {self.table_name} WHERE {key} = $1{type_cast}", value
            )
            return self.model(**self._adapt(record)) if record else None

    async def get_all(self, pool: asyncpg.Pool) -> List[T]:
        """Получение всех записей."""
        async with await self._acquire(pool) as conn:
            records = await conn.fetch(f"SELECT * FROM {self.table_name}")
            return [self.model(**self._adapt(r)) for r in records]

    async def filter(self, pool: asyncpg.Pool, order_by: Optional[str] = None, **kwargs) -> List[T]:
        """Получение нескольких записей по фильтру."""
        if len(kwargs) != 1:
            raise ValueError("Only one filter parameter is allowed")
        key, value = next(iter(kwargs.items()))
        # Проверяем, является ли поле bigint
        bigint_fields = self.BIGINT_FIELDS.get(self.table_name, set())
        type_cast = "::bigint" if key in bigint_fields else ""
        order_clause = f" ORDER BY {order_by}" if order_by else ""
        async with await self._acquire(pool) as conn:
            records = await conn.fetch(
                f"SELECT * FROM {self.table_name} WHERE {key} = $1{type_cast}{order_clause}", value
            )
            return [self.model(**self._adapt(r)) for r in records]

    async def delete(self, pool: asyncpg.Pool, **kwargs) -> bool:
        """Удаление записи по ID."""
        if not kwargs:
            return False
        key, value = next(iter(kwargs.items()))
        # Проверяем, является ли поле bigint
        bigint_fields = self.BIGINT_FIELDS.get(self.table_name, set())
        type_cast = "::bigint" if key in bigint_fields else ""
        async with await self._acquire(pool) as conn:
            result = await conn.execute(
                f"DELETE FROM {self.table_name} WHERE {key} = $1{type_cast}", value
            )
            return "DELETE 1" in result

    async def create(self, pool: asyncpg.Pool, **kwargs) -> bool:
        """Добавление новой записи.

        Args:
            pool (asyncpg.Pool | asyncpg.Connection): Пул или соединение с БД.
            **kwargs: Данные для новой записи.
        """
        # Получаем список bigint-полей для этой таблицы
        bigint_fields = self.BIGINT_FIELDS.get(self.table_name, set())

        # Строим placeholders с явным приведением типов для bigint
        placeholders = []
        typed_values = []
        for i, (key, value) in enumerate(kwargs.items(), start=1):
            if key in bigint_fields:
                placeholders.append(f"${i}::bigint")
            else:
                placeholders.append(f"${i}")
            typed_values.append(value)

        columns = ", ".join(kwargs.keys())
        placeholders_str = ", ".join(placeholders)
        query = f"""
            INSERT INTO {self.table_name} ({columns})
            VALUES ({placeholders_str})
            RETURNING 1
        """
        async with await self._acquire(pool) as conn:
            result = await conn.execute(query, *typed_values)
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

        # Получаем список bigint-полей для этой таблицы
        bigint_fields = self.BIGINT_FIELDS.get(self.table_name, set())

        # Строим set_clause с явным приведением типов для bigint
        typed_values = []
        set_parts = []
        param_idx = 2  # $1 зарезервирован для where_value
        for k in filtered_kwargs.keys():
            if k in bigint_fields:
                set_parts.append(f"{k} = ${param_idx}::bigint")
            else:
                set_parts.append(f"{k} = ${param_idx}")
            param_idx += 1
        set_clause = ", ".join(set_parts)

        # Формируем список значений: сначала where_value, потом остальные
        typed_values = [where_value] + [filtered_kwargs[k] for k in filtered_kwargs]

        # Определяем primary key для ON CONFLICT
        # Для таблицы keys это (tg_id, client_id)
        if self.table_name == "keys":
            # Используем UPSERT для обработки гонки
            set_clause_with_excluded = ", ".join(
                [f"{k} = EXCLUDED.{k}" for k in filtered_kwargs.keys()]
            )
            # Определяем тип для where_key (tg_id — bigint)
            where_type = "::bigint" if where_key in bigint_fields else ""
            query = f"""
                INSERT INTO {self.table_name} ({", ".join(filtered_kwargs.keys())}, {where_key})
                VALUES ({", ".join([f"${i + 2}" + ("::bigint" if list(filtered_kwargs.keys())[i-1] in bigint_fields else "") for i in range(len(filtered_kwargs))])}, $1{where_type})
                ON CONFLICT (tg_id, client_id) DO UPDATE SET {set_clause_with_excluded}
            """
            async with await self._acquire(pool) as conn:
                result = await conn.execute(query, *typed_values)
                return result == "INSERT 0 1" or result == "UPDATE 1"
        else:
            # Определяем тип для where_key
            where_type = "::bigint" if where_key in bigint_fields else ""
            query = f"""
                UPDATE {self.table_name}
                SET {set_clause}
                WHERE {where_key} = $1{where_type}
            """
            async with await self._acquire(pool) as conn:
                result = await conn.execute(query, *typed_values)
                return result == "UPDATE 1"
