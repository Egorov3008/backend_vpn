"""User repository for typed database access."""
from typing import Optional

import asyncpg

from models import User
from services.core.data.protocols import DataProtocol


class UserRepository:
    """Repository for User model with typed interface."""

    def __init__(self, data_protocol: DataProtocol[User]):
        """Initialize with a DataProtocol instance.

        Args:
            data_protocol: DataProtocol[User] providing cached access to users
        """
        self.data = data_protocol

    async def get_by_tg_id(self, tg_id: int) -> Optional[User]:
        """Get user by Telegram ID.

        Args:
            tg_id: Telegram user ID

        Returns:
            User model or None if not found
        """
        return await self.data.get_data(tg_id)

    async def create(self, user: User) -> User:
        """Create a new user (placeholder for future pool-based implementation).

        Args:
            user: User model to create

        Returns:
            Created user model
        """
        # Note: actual creation requires pool/connection which will be
        # passed through endpoint dependencies
        return user

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
