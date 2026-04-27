"""User repository for typed database access."""
import logging
from typing import Optional

import asyncpg

from models import User
from services.core.data.protocols import DataProtocol

logger = logging.getLogger(__name__)


class UserRepository:
    """Repository for User model with typed interface."""

    def __init__(self, data_protocol: DataProtocol[User], pool: Optional[asyncpg.Pool] = None):
        """Initialize with a DataProtocol instance.

        Args:
            data_protocol: DataProtocol[User] providing cached access to users
            pool: Optional asyncpg pool for database operations
        """
        self.data = data_protocol
        self.pool = pool

    async def get_by_tg_id(self, tg_id: int) -> Optional[User]:
        """Get user by Telegram ID.

        Args:
            tg_id: Telegram user ID

        Returns:
            User model or None if not found
        """
        return await self.data.get_data(tg_id)

    async def create(self, user: User, conn: Optional[asyncpg.Connection] = None) -> Optional[User]:
        """Create a new user in the database.

        Args:
            user: User model to create
            conn: Optional asyncpg connection (for transaction context)

        Returns:
            Created user model or None if creation failed
        """
        if not self.pool and not conn:
            logger.error("Cannot create user: no pool or connection available")
            return None

        query = """
            INSERT INTO users (tg_id, username, first_name, last_name, language_code,
                              server_id, balance, trial, is_admin, is_blocked, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            RETURNING *
        """

        try:
            if conn:
                row = await conn.fetchrow(
                    query,
                    user.tg_id,
                    user.username,
                    user.first_name,
                    user.last_name,
                    user.language_code,
                    user.server_id,
                    user.balance,
                    user.trial,
                    user.is_admin,
                    user.is_blocked,
                    user.created_at,
                    user.updated_at,
                )
            else:
                async with self.pool.acquire() as conn:
                    row = await conn.fetchrow(
                        query,
                        user.tg_id,
                        user.username,
                        user.first_name,
                        user.last_name,
                        user.language_code,
                        user.server_id,
                        user.balance,
                        user.trial,
                        user.is_admin,
                        user.is_blocked,
                        user.created_at,
                        user.updated_at,
                    )

            if row:
                created_user = User(**dict(row))
                logger.info(f"User created successfully: tg_id={user.tg_id}")
                return created_user
            else:
                logger.error(f"Failed to create user: tg_id={user.tg_id}")
                return None
        except Exception as e:
            logger.error(f"Error creating user tg_id={user.tg_id}: {e}")
            raise

    async def exists(self, tg_id: int) -> bool:
        """Check if user exists by Telegram ID.

        Args:
            tg_id: Telegram user ID

        Returns:
            True if user exists, False otherwise
        """
        return await self.data.exists(tg_id)

    async def get_all(self) -> list[User]:
        """Get all users.

        Returns:
            List of all User models
        """
        return await self.data.get_all()
