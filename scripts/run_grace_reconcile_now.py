# scripts/run_grace_reconcile_now.py
"""One-shot: converge every subscription key's panel inbound set to its
DB-derived status RIGHT NOW (same logic as the hourly run_grace_transitions
job, but invoked manually). Used to apply the set_inbounds fix immediately
without waiting for the next hourly tick.

  ACTIVE  -> set_inbounds(paid_inbound_ids())  (detaches stale overlay, e.g. 6)
  GRACE   -> set_inbounds(grace_inbound_ids()) (detaches paid overlay -> [7])
  EXPIRED -> expire_after_grace()              (delete panel client + cache)

DB rows are NOT touched. Reads fixed code from disk (fresh process), so it
works even before the long-running uvicorn is restarted.

Usage:
    docker compose exec backend python scripts/run_grace_reconcile_now.py
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.base import create_db_pool  # noqa: E402
from database.service import DataService  # noqa: E402
from services.cache.loader import LoadingService  # noqa: E402
from services.cache.service import CacheService  # noqa: E402
from services.cache.storage import CacheStorage  # noqa: E402
from services.core.data.service import ServiceDataModel  # noqa: E402
from services.core.keys.utils.status import KeyStatus  # noqa: E402
from app.factories import build_grace_manager  # noqa: E402
from config import settings  # noqa: E402
from logger import setup_logging  # noqa: E402


async def main() -> int:
    setup_logging(log_level=settings.log_level, log_file=settings.log_file or None,
                  log_format=settings.log_format)
    pool = await create_db_pool()
    storage = CacheStorage()
    await storage.start()
    cache_service = CacheService(storage)
    try:
        data_service = DataService()
        loader = LoadingService(cache=cache_service, data_service=data_service, pool=pool)
        await loader.loading()
        service_data = ServiceDataModel(cache_service=cache_service, data_service=data_service)
        grace = build_grace_manager(pool=pool, service_data=service_data,
                                     cache=cache_service, data_service=data_service)

        keys = await service_data.keys.get_all()
        now_ms = int(time.time() * 1000)
        counts = {"ACTIVE": 0, "GRACE": 0, "EXPIRED": 0, "NONE": 0, "skipped": 0}
        ok = fail = 0
        failures = []
        for key in keys or []:
            if getattr(key, "grace_expiry", None) is None:
                counts["skipped"] += 1
                continue
            st = KeyStatus.of(key, now_ms)
            counts[st] = counts.get(st, 0) + 1
            if st == KeyStatus.NONE:
                continue
            try:
                res = await grace.reconcile(key)
                if res:
                    ok += 1
                else:
                    fail += 1
                    failures.append((key.email, st, "reconcile returned False"))
            except Exception as e:
                fail += 1
                failures.append((key.email, st, str(e)))

        print("\n=== reconcile summary ===")
        print(f"  by status: {counts}")
        print(f"  ok={ok}  fail={fail}")
        if failures:
            print(f"  failures ({len(failures)}):")
            for email, st, err in failures:
                print(f"    {email:18} {st:8} {err}")
        return 0
    finally:
        await storage.stop()
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))