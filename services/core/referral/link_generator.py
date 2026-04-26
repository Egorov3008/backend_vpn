import uuid

import asyncpg

from config import BOT_NAME
from logger import logger
from models import ReferralLink
from services.cache.key_manager import CacheKeyManager
from services.core.data.service import ServiceDataModel


class ReferralLinkGenerator:
    """Генерация и управление реферальными ссылками."""

    def __init__(self, model_data: ServiceDataModel):
        self._referral_data = model_data.referral_links
        self._keys = CacheKeyManager()

    async def get_or_create(self, conn: asyncpg.Pool, tg_id: int) -> ReferralLink:
        """Получить существующую ссылку или создать новую."""
        existing = await self._referral_data.get_by(referrer_tg_id=tg_id)
        if existing:
            return existing

        token = self._generate_token()
        link = ReferralLink(referrer_tg_id=tg_id, token=token)
        await self._referral_data.save_data(conn, link, token=link.token)
        logger.info("Реферальная ссылка создана", tg_id=tg_id, token=token)
        return link

    def get_share_url(self, token: str) -> str:
        """Формирует URL для шаринга."""
        return f"https://t.me/{BOT_NAME}?start={token}"

    @staticmethod
    def _generate_token() -> str:
        """Генерирует уникальный короткий токен."""
        return f"ref_{uuid.uuid4().hex[:12]}"
