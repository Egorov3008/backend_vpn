"""Апгрейд лендинг-ключа (trial/paid) — переносит клиента на полный inbound-набор
и продлевает expiry_time напрямую в панели, без grace-отступа."""
from typing import Optional

from asyncpg import Pool

from client import XUISession
from logger import logger
from models import Key, Tariff
from services.cache.key_manager import CacheKeyManager
from services.cache.service import CacheService
from services.core.data.service import ServiceDataModel
from services.core.keys.utils.inbounds import paid_inbound_ids


async def upgrade_landing_key(
    xui_session: XUISession,
    model_data: ServiceDataModel,
    cache: CacheService,
    pool: Pool,
    key: Key,
    tariff: Tariff,
    new_expiry: int,
    transfer_tg: bool = True,
) -> Optional[Key]:
    """Апгрейд лендинг-ключа: baseline+overlay в панели, expiry_time = new_expiry.

    transfer_tg=True переносит владельца на key.converted_tg_id (обычный сценарий
    claim с лендинга — тот же клиент, что жил на псевдо-tg_id).
    """
    if not await xui_session.set_inbounds(key.email, paid_inbound_ids()):
        logger.error("upgrade_landing_key: set_inbounds провален", extra={"email": key.email})
        return None

    saved_expiry = key.expiry_time
    key.expiry_time = new_expiry
    if not await xui_session.extend_client_key(key):
        logger.error("upgrade_landing_key: extend_client_key провален", extra={"email": key.email})
        key.expiry_time = saved_expiry
        return None

    key.tariff_id = tariff.id
    key.name_tariff = tariff.name_tariff
    key.period = tariff.period
    key.amount = tariff.amount
    key.limit_ip = tariff.limit_ip or key.limit_ip
    key.notified_24h = False
    key.notified_10h = False
    key.notified_expired_grace = False
    if transfer_tg and key.converted_tg_id:
        key.tg_id = key.converted_tg_id

    await model_data.keys.update(pool, key, search_data={"email": key.email})
    await cache.keys.set(CacheKeyManager.key(key.email), key)
    logger.info("upgrade_landing_key", extra={"email": key.email, "new_expiry": new_expiry})
    return key
