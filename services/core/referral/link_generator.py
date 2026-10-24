import uuid
from typing import Optional
from urllib.parse import quote

import asyncpg

from config import settings
from logger import logger
from models import ReferralLink
from services.core.data.service import ServiceDataModel

# Параметр, который читает сайт (platform-web, src/referral.ts).
REF_QUERY_PARAM = "ref"


def referral_share_url(token: str, site_url: Optional[str] = None) -> Optional[str]:
    """Ссылка-приглашение на сайт: ``<WEB_PUBLIC_URL>/?ref=<token>``.

    Новые пользователи приходят только через сайт: там реферер фиксируется
    подписанной cookie ``tg_ref`` и засчитывается при входе через бота
    (``/web/auth/bot-login/complete``). Ссылок в бота (``t.me/<бот>?start=ref_…``)
    больше нет. Без ``WEB_PUBLIC_URL`` возвращает None: клиенты покажут, что
    ссылка недоступна, а не соберут адрес сами.
    """
    base = (settings.web_public_url if site_url is None else site_url).strip().rstrip("/")
    if not base:
        return None
    return f"{base}/?{REF_QUERY_PARAM}={quote(token, safe='')}"


class ReferralLinkGenerator:
    """Создание реферальных токенов (одна ссылка на пользователя)."""

    def __init__(self, model_data: ServiceDataModel):
        self._referral_data = model_data.referral_links

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

    @staticmethod
    def _generate_token() -> str:
        """Генерирует уникальный короткий токен."""
        return f"ref_{uuid.uuid4().hex[:12]}"
