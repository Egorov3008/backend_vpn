from dataclasses import dataclass

import asyncpg

from database.base import BaseRepository


@dataclass
class REGISTRATE_USER:
    tg_id: int
    is_msg: bool


class AuthRepository(BaseRepository[REGISTRATE_USER]):
    def __init__(self):
        super().__init__(table_name="registrate_msg_user", model=REGISTRATE_USER)

    async def check_connection_exists(self, conn: asyncpg.Connection, tg_id: int):
        """
        Проверяет существование подключения для указанного пользователя в базе данных.

        Args:
            tg_id (int): Telegram ID пользователя для проверки.

        """
        exists = await conn.fetchval(
            """
            SELECT EXISTS(SELECT 1 FROM users WHERE tg_id = $1)
            """,
            tg_id,
        )
        return exists

    async def get_status_msg(self, conn: asyncpg.Connection, tg_id):
        result = await conn.fetch(
            """SELECT * FROM registrate_msg_user WHERE tg_id = $1""", tg_id
        )

        return int(result[0]["is_msg"]) if result else 0

    async def upsert_user_message_status(self, conn: asyncpg.Connection, tg_id: int):
        """Добавляет или обновляет статус подачи заявки пользователя"""
        query = """
        INSERT INTO registrate_msg_user (tg_id, is_msg)
        VALUES ($1, 0)
        ON CONFLICT (tg_id) 
        DO UPDATE SET 
            is_msg = LEAST(registrate_msg_user.is_msg + 1, 2)
        RETURNING is_msg
        """
        return await conn.fetchval(query, tg_id)
