# scripts/migrate_grace.py
"""One-shot backfill: bring existing paid/trial keys into the grace model.

For each key with grace_expiry IS NULL and a subscription tariff
(paid OR trial — see inbounds.is_subscription):
  1. set_inbounds(paid_inbound_ids()) — ensure baseline inbound 7 + paid overlay attached.
  2. set panel expiryTime = grace_expiry (pre-emptive, via extend_client_key).
  3. write grace_expiry = expiry_time + GRACE_PERIOD_MS to DB; preserve expiry_time (paid expiry).

Idempotent: skips keys already having grace_expiry. --dry-run prints only, changes nothing.
Runs inside the backend container (cwd=/app), like sweep_pending_payments.py.

Usage:
    docker compose exec backend python scripts/migrate_grace.py --dry-run
    docker compose exec backend python scripts/migrate_grace.py
"""
import argparse
import asyncio
import sys
from pathlib import Path

# /app (backend root) в sys.path — те же импорты, что у sweep_pending_payments.py
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.base import create_db_pool  # noqa: E402
from database.service import DataService  # noqa: E402
from services.cache.loader import LoadingService  # noqa: E402
from services.cache.service import CacheService  # noqa: E402
from services.cache.storage import CacheStorage  # noqa: E402
from services.core.data.service import ServiceDataModel  # noqa: E402
from client import XUISession  # noqa: E402
from config import settings  # noqa: E402
from logger import setup_logging  # noqa: E402
from services.core.keys.utils.inbounds import (  # noqa: E402
    paid_inbound_ids, GRACE_PERIOD_MS, is_subscription,
)


async def _run(dry_run: bool) -> int:
    setup_logging(
        log_level=settings.log_level,
        log_file=settings.log_file or None,
        log_format=settings.log_format,
    )
    pool = await create_db_pool()
    storage = CacheStorage()
    await storage.start()
    cache_service = CacheService(storage)
    migrated = skipped = failed = 0
    try:
        data_service = DataService()
        loader = LoadingService(cache=cache_service, data_service=data_service, pool=pool)
        await loader.loading()
        service_data = ServiceDataModel(cache_service=cache_service, data_service=data_service)

        xui = XUISession(model_service=service_data, loading=loader)

        keys = await service_data.keys.get_all()
        for key in keys or []:
            if getattr(key, "grace_expiry", None) is not None:
                skipped += 1
                continue
            tariff = await service_data.tariffs.get_data(
                int(getattr(key, "tariff_id", 0) or 0), pool
            )
            if not is_subscription(tariff):
                skipped += 1
                continue
            original_expiry = int(getattr(key, "expiry_time", 0) or 0)
            if original_expiry <= 0:
                print(f"skip {key.email}: no expiry_time")
                skipped += 1
                continue
            grace_exp = original_expiry + GRACE_PERIOD_MS
            print(
                f"{'[dry] ' if dry_run else ''}migrate {key.email}: "
                f"set_inbounds({paid_inbound_ids()}), grace_expiry={grace_exp}, "
                f"panel expiryTime={grace_exp}"
            )
            if dry_run:
                migrated += 1
                continue

            # 1. Converge panel inbounds to paid set (baseline 7 + overlay).
            if not await xui.set_inbounds(key.email, paid_inbound_ids()):
                print(f"  FAIL set_inbounds — skip (retry next run)")
                failed += 1
                continue
            # 2. Panel expiryTime = grace_exp (pre-emptive). extend_client_key reads
            #    key.expiry_time, so set it to grace_exp for the call, then RESTORE
            #    the paid expiry before the DB write (mirrors GraceManager._apply_paid).
            key.expiry_time = grace_exp
            if not await xui.extend_client_key(key):
                print(f"  FAIL extend_client_key — skip (retry next run)")
                failed += 1
                continue
            # 3. DB: preserve paid expiry, set grace_expiry. keys.update also writes cache.
            key.expiry_time = original_expiry
            key.grace_expiry = grace_exp
            await service_data.keys.update(pool, key, search_data={"email": key.email})
            migrated += 1
    finally:
        await storage.stop()
        await pool.close()

    print(f"done: migrated={migrated} skipped={skipped} failed={failed} dry_run={dry_run}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Backfill existing keys into the grace model.")
    p.add_argument("--dry-run", action="store_true", help="Только отчёт, без изменений (по умолчанию).")
    args = p.parse_args()
    return asyncio.run(_run(args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
