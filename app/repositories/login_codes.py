"""Login code repository for typed database access."""
from typing import Optional

import asyncpg

from models import LoginCode
from services.core.data.protocols import DataProtocol


class LoginCodeRepository:
    """Repository for LoginCode model with typed interface."""

    def __init__(self, data_protocol: DataProtocol[LoginCode]):
        """Initialize with a DataProtocol instance.

        Args:
            data_protocol: DataProtocol[LoginCode] providing cached access to login codes
        """
        self.data = data_protocol

    async def create(
        self,
        code: str,
        tg_id: int,
        expires_at,
    ) -> LoginCode:
        """Create a new login code (placeholder for future pool-based implementation).

        Args:
            code: The login code string
            tg_id: Telegram user ID associated with code
            expires_at: Datetime when code expires

        Returns:
            Created LoginCode model
        """
        # Note: actual creation requires pool/connection which will be
        # passed through endpoint dependencies
        return LoginCode(code=code, tg_id=tg_id, expires_at=expires_at)

    async def get_by_code(self, code: str) -> Optional[LoginCode]:
        """Get login code by code string (placeholder for future implementation).

        Args:
            code: The login code string

        Returns:
            LoginCode model or None if not found
        """
        # This would require a query by non-primary-key field
        # For now, returns None as placeholder
        return None

    async def get_by_tg_id(self, tg_id: int) -> Optional[LoginCode]:
        """Get most recent login code for user (placeholder for future implementation).

        Args:
            tg_id: Telegram user ID

        Returns:
            LoginCode model or None if not found
        """
        # This would require a query that filters by tg_id
        # For now, returns None as placeholder
        return None

    async def get_all(self) -> list[LoginCode]:
        """Get all login codes.

        Returns:
            List of all LoginCode models
        """
        return await self.data.get_all()
