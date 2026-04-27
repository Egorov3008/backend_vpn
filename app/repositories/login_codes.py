"""Login code repository for typed database access."""
import logging
from typing import Optional
from datetime import datetime

import asyncpg

from models import LoginCode
from services.core.data.protocols import DataProtocol

logger = logging.getLogger(__name__)


class LoginCodeRepository:
    """Repository for LoginCode model with typed interface."""

    def __init__(self, data_protocol: DataProtocol[LoginCode], pool: Optional[asyncpg.Pool] = None):
        """Initialize with a DataProtocol instance.

        Args:
            data_protocol: DataProtocol[LoginCode] providing cached access to login codes
            pool: Optional asyncpg pool for database operations
        """
        self.data = data_protocol
        self.pool = pool

    async def create(
        self,
        code: str,
        tg_id: int,
        expires_at: datetime,
        conn: Optional[asyncpg.Connection] = None,
    ) -> Optional[LoginCode]:
        """Create a new login code in the database.

        Args:
            code: The login code string
            tg_id: Telegram user ID associated with code
            expires_at: Datetime when code expires
            conn: Optional asyncpg connection (for transaction context)

        Returns:
            Created LoginCode model or None if creation failed
        """
        if not self.pool and not conn:
            logger.error("Cannot create login code: no pool or connection available")
            return None

        query = """
            INSERT INTO login_codes (code, tg_id, expires_at, used, created_at)
            VALUES ($1, $2, $3, FALSE, $4)
            RETURNING *
        """

        try:
            now = datetime.now()
            if conn:
                row = await conn.fetchrow(query, code, tg_id, expires_at, now)
            else:
                async with self.pool.acquire() as conn:
                    row = await conn.fetchrow(query, code, tg_id, expires_at, now)

            if row:
                created_code = LoginCode(**dict(row))
                logger.info(f"Login code created successfully: tg_id={tg_id}, code={code[:4]}...")
                return created_code
            else:
                logger.error(f"Failed to create login code for tg_id={tg_id}")
                return None
        except Exception as e:
            logger.error(f"Error creating login code for tg_id={tg_id}: {e}")
            raise

    async def get_by_code(self, code: str) -> Optional[LoginCode]:
        """Get login code by code string.

        Args:
            code: The login code string

        Returns:
            LoginCode model or None if not found
        """
        if not self.pool:
            logger.error("Cannot get login code: no pool available")
            return None

        query = "SELECT * FROM login_codes WHERE code = $1 AND used = FALSE AND expires_at > NOW()"

        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(query, code)
                if row:
                    login_code = LoginCode(**dict(row))
                    logger.info(f"Login code retrieved: code={code[:4]}...")
                    return login_code
                else:
                    logger.debug(f"Login code not found or expired: code={code[:4]}...")
                    return None
        except Exception as e:
            logger.error(f"Error retrieving login code: {e}")
            raise

    async def get_by_tg_id(self, tg_id: int) -> list[LoginCode]:
        """Get login codes for a user.

        Args:
            tg_id: Telegram user ID

        Returns:
            List of LoginCode models for the user
        """
        if not self.pool:
            logger.error("Cannot get login codes: no pool available")
            return []

        query = "SELECT * FROM login_codes WHERE tg_id = $1 ORDER BY created_at DESC"

        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(query, tg_id)
                codes = [LoginCode(**dict(row)) for row in rows]
                logger.info(f"Retrieved {len(codes)} login codes for tg_id={tg_id}")
                return codes
        except Exception as e:
            logger.error(f"Error retrieving login codes for tg_id={tg_id}: {e}")
            raise

    async def get_all(self) -> list[LoginCode]:
        """Get all login codes.

        Returns:
            List of all LoginCode models
        """
        return await self.data.get_all()
