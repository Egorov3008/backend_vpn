"""Разовый обход pending-платежей: опрос YooKassa + (опционально) маршрутизация.

Запускать внутри контейнера backend::

    # Сухой прогон — показать, какие платежи YooKassa подтверждает как succeeded
    docker compose exec backend python scripts/sweep_pending_payments.py

    # Применить: маршрутизировать succeeded (продлить/создать ключи)
    docker compose exec backend python scripts/sweep_pending_payments.py --apply

    # Исключить уже обработанные вручную (напр. gd3ms5 — админ продлил сам):
    docker compose exec backend python scripts/sweep_pending_payments.py --apply \
        --exclude 31caf672-000f-5000-b000-14d0d29e483c

    # Только свежие / только после даты:
    docker compose exec backend python scripts/sweep_pending_payments.py --max-age 1440
    docker compose exec backend python scripts/sweep_pending_payments.py --since "2026-06-22T19:00:00+00:00"

По умолчанию dry_run=True — ничего не меняет, только отчёт.
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

# /app (backend root) в sys.path, чтобы импорты `config`, `services`, `app` работали
# при запуске `python scripts/sweep_pending_payments.py` из cwd=/app.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.base import create_db_pool  # noqa: E402
from database.service import DataService  # noqa: E402
from services.cache.loader import LoadingService  # noqa: E402
from services.cache.service import CacheService  # noqa: E402
from services.cache.storage import CacheStorage  # noqa: E402
from services.core.data.service import ServiceDataModel  # noqa: E402
from services.core.payment.pending_sweep import sweep_pending_payments  # noqa: E402
from logger import setup_logging  # noqa: E402
from config import settings  # noqa: E402


def _parse_exclude(raw: str) -> set[str]:
    return {s.strip() for s in raw.split(",") if s.strip()}


def _parse_since(raw: str | None) -> datetime | None:
    if not raw:
        return None
    return datetime.fromisoformat(raw)


async def _run(args: argparse.Namespace) -> int:
    setup_logging(
        log_level=settings.log_level,
        log_file=settings.log_file or None,
        log_format=settings.log_format,
    )

    pool = await create_db_pool()
    storage = CacheStorage()
    await storage.start()
    cache_service = CacheService(storage)
    try:
        data_service = DataService()
        loader = LoadingService(cache=cache_service, data_service=data_service, pool=pool)
        await loader.loading()
        service_data = ServiceDataModel(cache_service=cache_service, data_service=data_service)

        report = await sweep_pending_payments(
            pool,
            service_data,
            cache_service,
            dry_run=not args.apply,
            max_age_minutes=args.max_age,
            since=_parse_since(args.since),
            exclude_ids=_parse_exclude(args.exclude),
            only_ids=_parse_exclude(args.only),
            limit=args.limit,
        )
    finally:
        await storage.stop()
        await pool.close()

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Sweep pending payments via YooKassa.")
    p.add_argument("--apply", action="store_true", help="Маршрутизировать succeeded (по умолчанию dry-run).")
    p.add_argument("--exclude", default="", help="CSV payment_id для пропуска (уже обработанные вручную).")
    p.add_argument("--only", default="", help="CSV payment_id — обработать только их (точечный route, остальное не трогать).")
    p.add_argument("--max-age", type=int, default=None, help="Только платежи младше N минут.")
    p.add_argument("--since", default=None, help="Только платежи созданные после ISO-даты (напр. 2026-06-22T19:00:00+00:00).")
    p.add_argument("--limit", type=int, default=None, help="Ограничить кол-во.")
    args = p.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())