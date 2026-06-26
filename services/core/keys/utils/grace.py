"""GraceManager — transitions a Key/Client between active/grace/expired
by attaching/detaching 3x-ui inbounds. Panel expiryTime is pre-set to
grace_expiry at creation, so active->grace is just a detach."""
from typing import Optional

from client import XUISession
from logger import logger
from models import Key, Tariff
from services.cache.key_manager import CacheKeyManager
from services.cache.service import CacheService
from services.core.data.service import ServiceDataModel
from services.core.keys.utils.calculator import ExpiryCalculator
from services.core.keys.utils.inbounds import (
    paid_inbound_ids, grace_inbound_ids, expired_inbound_ids,
    expected_inbound_ids, GRACE_PERIOD_MS,
)
from services.core.keys.utils.status import KeyStatus


class GraceManager:
    def __init__(
        self,
        xui_session: XUISession,
        model_data: ServiceDataModel,
        cache: CacheService,
        expiry: ExpiryCalculator,
        pool,
    ):
        self.xui = xui_session
        self.key_data = model_data.keys
        self.cache = cache
        self.expiry = expiry
        self.pool = pool

    async def enter_grace(self, key: Key) -> bool:
        ok = await self.xui.set_inbounds(key.email, grace_inbound_ids())
        await self.cache.keys.set(CacheKeyManager.key(key.email), key)
        logger.info("enter_grace", extra={"email": key.email, "ok": ok})
        return ok

    async def expire_after_grace(self, key: Key) -> bool:
        await self.xui.set_inbounds(key.email, expired_inbound_ids())
        deleted = True
        try:
            await self.xui.delete_client(key.email, 0, key.client_id)
        except Exception as e:
            if "not found" in str(e).lower():
                # Client already gone on the panel — treat as deleted.
                logger.info("expire_after_grace: клиент уже удалён",
                            extra={"email": key.email})
            else:
                # Real panel failure (network/auth/5xx) — do NOT claim
                # success: leave the cache entry so a later run can retry.
                logger.warning("expire_after_grace: delete провален",
                               extra={"email": key.email, "error": str(e)})
                deleted = False
        if deleted:
            await self.cache.keys.delete(CacheKeyManager.key(key.email))
        logger.info("expire_after_grace", extra={"email": key.email, "deleted": deleted})
        return deleted

    async def renew_from_grace(self, key: Key, tariff: Tariff,
                               number_of_months: int = 1) -> Optional[Key]:
        new_expiry = self.expiry.key_duration(key, tariff.period, number_of_months)
        return await self._apply_paid(key, tariff, new_expiry, number_of_months,
                                       transfer_tg=False)

    async def upgrade_from_landing(self, key: Key, tariff: Tariff,
                                    number_of_months: int = 1) -> Optional[Key]:
        new_expiry = self.expiry.key_duration_new_key(tariff.period, number_of_months)
        return await self._apply_paid(key, tariff, new_expiry, number_of_months,
                                       transfer_tg=True)

    async def _apply_paid(self, key: Key, tariff: Tariff, new_expiry: int,
                          number_of_months: int, transfer_tg: bool) -> Optional[Key]:
        grace_exp = new_expiry + GRACE_PERIOD_MS
        # 1. Converge panel inbounds to paid set.
        if not await self.xui.set_inbounds(key.email, paid_inbound_ids()):
            logger.error("_apply_paid: set_inbounds провален", extra={"email": key.email})
            return None
        # 2. Set panel expiryTime = grace_expiry (pre-emptive), enable=True.
        #    extend_client_key reads key.expiry_time but does not mutate it,
        #    so save the pre-extend value and restore it on failure (the key
        #    may be the live cached object — don't leave it at grace_exp).
        saved_expiry = key.expiry_time
        key.expiry_time = grace_exp
        if not await self.xui.extend_client_key(key):
            logger.error("_apply_paid: extend_client_key провален", extra={"email": key.email})
            key.expiry_time = saved_expiry
            return None
        # 3. DB: store paid expiry + planned grace + tariff fields.
        key.expiry_time = new_expiry
        key.grace_expiry = grace_exp
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
        await self.key_data.update(self.pool, key, search_data={"email": key.email})
        await self.cache.keys.set(CacheKeyManager.key(key.email), key)
        logger.info("_apply_paid", extra={"email": key.email, "new_expiry": new_expiry,
                                           "grace_expiry": grace_exp, "transfer_tg": transfer_tg})
        return key

    async def reconcile(self, key: Key) -> bool:
        st = KeyStatus.of(key)
        target = expected_inbound_ids(st)
        ok = await self.xui.set_inbounds(key.email, target)
        if st == KeyStatus.EXPIRED:
            # client should be gone; ensure cache reflects it
            await self.cache.keys.delete(CacheKeyManager.key(key.email))
        else:
            await self.cache.keys.set(CacheKeyManager.key(key.email), key)
        return ok
