"""Sweep pending payments: ask YooKassa and route the succeeded ones.

Safety net for missed YooKassa webhooks + one-off backlog recovery tool.

Центральная логика переиспользуется:
  * фоновым поллингом из ``background.scheduler`` (авто, только свежие платежи,
    с exclude-списком);
  * разовым CLI-скриптом ``scripts/sweep_pending_payments.py`` (dry-run + --apply).

Идемпотентность: ``PaymentRouter.route`` сам пропускает платежи со статусом
``succeeded`` (router.py). Но платеж, обработанный админом *вручную* (продление
ключа без обновления статуса), остаётся ``pending`` — повторный route продлит
ключ ещё раз. Поэтому такие payment_id передают через ``exclude_ids``.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import asyncpg

from app.factories import build_payment_router
from config import settings
from database.service import DataService
from logger import logger
from models import PaymentModel
from services.cache.service import CacheService
from services.core.data.service import ServiceDataModel
from services.core.notifications import TelegramBotNotifier


def _notifier() -> TelegramBotNotifier:
    """Тот же notifier, что в api.v1.payments — чтобы продление/создание шло с уведомлением."""
    return TelegramBotNotifier(
        bot_token=settings.bot_token,
        support_chat_url=settings.support_chat_url,
    )


async def list_pending_payments(
    pool: asyncpg.Pool,
    max_age_minutes: Optional[int] = None,
    since: Optional[datetime] = None,
    limit: Optional[int] = None,
) -> list[dict]:
    """Возвращает pending-платежи из БД.

    ``max_age_minutes`` — ограничить свежими (created_at >= now - max_age).
    ``since`` — только созданные после указанного момента (напр. после починки webhook).
    Можно комбинировать; ``limit`` ограничивает кол-во.
    """
    conditions = ["status = 'pending'"]
    params: list[Any] = []
    if max_age_minutes is not None:
        params.append(datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes))
        conditions.append(f"created_at >= ${len(params)}")
    if since is not None:
        params.append(since)
        conditions.append(f"created_at >= ${len(params)}")
    where = " AND ".join(conditions)

    query = (
        f"SELECT payment_id, tg_id, payment_type, amount, created_at "
        f"FROM payments WHERE {where} ORDER BY created_at DESC"
    )
    if limit is not None:
        query += f" LIMIT {int(limit)}"

    rows = await pool.fetch(query, *params)
    return [dict(r) for r in rows]


async def _check_yookassa_status(payment_id: str) -> Optional[str]:
    """Спрашивает YooKassa статус платежа. Возвращает 'succeeded'|'pending'|'canceled'|... или None при ошибке."""
    import yookassa

    yookassa.Configuration.account_id = settings.yookassa_shop_id
    yookassa.Configuration.secret_key = settings.yookassa_secret_key
    try:
        yk = yookassa.Payment.find_one(payment_id)
        return getattr(yk, "status", None)
    except Exception as e:
        logger.warning(
            "Sweep: YooKassa find_one failed",
            payment_id=payment_id,
            error=str(e),
        )
        return None


async def _mark_canceled(
    pool: asyncpg.Pool,
    service_data: ServiceDataModel,
    payment_id: str,
) -> None:
    """Пометить платёж canceled в БД и кеше (зеркало логики из get_payment_status)."""
    payment = await service_data.payments.get_data(payment_id, conn=pool)
    if not payment:
        logger.warning("Sweep: платеж не найден для отмены", payment_id=payment_id)
        return
    canceled = PaymentModel(
        payment_id=payment.payment_id,
        tg_id=payment.tg_id,
        amount=payment.amount,
        payment_type=payment.payment_type,
        status="canceled",
        number_of_months=payment.number_of_months,
        discount_percent=payment.discount_percent,
        referral_discount=payment.referral_discount,
        balance_discount=payment.balance_discount,
    )
    await service_data.payments.update(pool, canceled, search_data={"payment_id": payment_id})
    logger.info("Sweep: платёж помечен canceled", payment_id=payment_id)


async def sweep_pending_payments(
    pool: asyncpg.Pool,
    service_data: ServiceDataModel,
    cache: CacheService,
    *,
    dry_run: bool = False,
    max_age_minutes: Optional[int] = None,
    since: Optional[datetime] = None,
    exclude_ids: Optional[set[str]] = None,
    only_ids: Optional[set[str]] = None,
    limit: Optional[int] = None,
) -> dict:
    """Опросить YooKassa по pending-платежам и обработать succeeded.

    Args:
        dry_run: если True — только отчёт, без route/обновления статуса.
        max_age_minutes: только платежи младше (для авто-поллинга).
        since: только платежи созданные после момента (напр. после починки webhook).
        exclude_ids: payment_id, которые пропустить (уже обработаны вручную).
        only_ids: если задано — обработать только эти payment_id (точечный route,
            напр. только renewal-платежи), остальной pending не трогается.
        limit: ограничение кол-ва.

    Returns:
        Отчёт: {checked, succeeded, canceled, pending, unknown, excluded, routed, errors}.
    """
    exclude_ids = set(exclude_ids or [])
    only_ids = set(only_ids) if only_ids else None
    rows = await list_pending_payments(pool, max_age_minutes=max_age_minutes, since=since, limit=limit)
    if only_ids is not None:
        rows = [r for r in rows if r["payment_id"] in only_ids]

    report: dict = {
        "checked": 0,
        "succeeded": [],
        "canceled": [],
        "pending": [],
        "unknown": [],
        "excluded": [],
        "routed": 0,
        "errors": [],
        "dry_run": dry_run,
    }

    data_service = DataService()
    notifier = _notifier()

    for row in rows:
        pid: str = row["payment_id"]
        report["checked"] += 1

        if pid in exclude_ids:
            report["excluded"].append(pid)
            continue

        yk_status = await _check_yookassa_status(pid)

        if yk_status is None:
            report["unknown"].append(pid)
            continue

        if yk_status == "succeeded":
            report["succeeded"].append(pid)
            if dry_run:
                continue
            try:
                router = build_payment_router(pool, service_data, cache, data_service, notifier=notifier)
                await router.route(pid)
                report["routed"] += 1
                logger.info("Sweep: платёж обработан (route)", payment_id=pid)
            except Exception as e:
                logger.error("Sweep: route failed", payment_id=pid, error=str(e), exc_info=True)
                report["errors"].append({"payment_id": pid, "error": str(e)})

        elif yk_status == "canceled":
            report["canceled"].append(pid)
            if dry_run:
                continue
            try:
                await _mark_canceled(pool, service_data, pid)
            except Exception as e:
                logger.error("Sweep: mark canceled failed", payment_id=pid, error=str(e), exc_info=True)
                report["errors"].append({"payment_id": pid, "error": str(e)})

        else:
            # pending / waiting_for_capture / прочее — оставляем как есть
            report["pending"].append(pid)

    logger.info(
        "Sweep завершён",
        checked=report["checked"],
        succeeded=len(report["succeeded"]),
        canceled=len(report["canceled"]),
        pending=len(report["pending"]),
        unknown=len(report["unknown"]),
        excluded=len(report["excluded"]),
        routed=report["routed"],
        errors=len(report["errors"]),
        dry_run=dry_run,
    )
    return report