import asyncpg

from database.auth.repositories import AuthRepository
from logger import logger


class AuthService:
    def __init__(self):
        self.repo_auth = AuthRepository()

    async def check_user_in_db_srv(self, conn: asyncpg.Connection, tg_id: int):
        """Проверяет является пользователь зарегестрированным и проверяет количество попыток на регистрацию"""
        result = await self.repo_auth.check_connection_exists(conn, tg_id)
        logger.debug("Статус пользователя для регистрации", tg_id=tg_id, result=result)
        return result

    async def check_connection_exists_srv(self, conn: asyncpg.Connection, tg_id: int):
        """Проверяет существование подключения для указанного пользователя в базе данных."""
        result = await self.repo_auth.check_connection_exists(conn, tg_id)
        logger.info(
            "Статус подключения пользователя в БД",
            tg_id=tg_id,
            result=result
        )
        return result

    async def get_status_msg_srv(self, conn: asyncpg.Connection, tg_id):
        """Получает статус метки при регистрации в боте"""
        result = await self.repo_auth.get_status_msg(conn, tg_id)
        if int(result) == 2:
            logger.info(
                "Пользователь потратил попытки для регистрации",
                tg_id=tg_id,
                result=result,
            )

        return result

    async def update_user_message_status_srv(
        self, conn: asyncpg.Connection, tg_id: int
    ):
        """Добавляет пользователя, если его нет, возвращает статус регистрации, если он есть"""
        status = await self.repo_auth.upsert_user_message_status(conn, tg_id)
        logger.info(
            "Статус пользователя для регистрации обновлен", tg_id=tg_id, status=status
        )
        return status

    async def insert_user_reg(self, conn: asyncpg.Connection, tg_id: int):
        """Добавляет пользователя"""
        await self.repo_auth.insert_user_reg(conn, tg_id)


auth_srv = AuthService()

# async def main():
#     async with asyncpg.create_pool(dsn=DATABASE_URL) as pool:
#         async with pool.acquire() as conn:
#             check = await auth_srv.check_user_in_db_srv(conn, 836250705)
#             logger.debug(check)
#
# asyncio.run(main())
