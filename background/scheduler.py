"""APScheduler setup for backend background tasks."""

import asyncio
import asyncpg
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from zoneinfo import ZoneInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from logger import logger
from services.core.data.service import ServiceDataModel

_OPERATION_LABELS = {"create_key": "Новые ключи", "renew_key": "Продления"}


def _format_daily_payments_report(report, tz: ZoneInfo) -> str:
    """HTML-текст сводки для админов (см. SyncScheduler.run_daily_payments_report)."""
    start = report.period_start.astimezone(tz).strftime("%d.%m %H:%M")
    end = report.period_end.astimezone(tz).strftime("%d.%m %H:%M")
    lines = [
        "📊 <b>Успешные платежи за 24 ч</b>",
        f"Период: {start} — {end} ({tz.key})",
        "",
        f"Платежей: <b>{report.count}</b>",
        f"Сумма: <b>{report.total:.2f} RUB</b>",
        f"Средний чек: {report.avg:.2f} RUB",
    ]
    if report.by_operation:
        lines.append("")
        for op in report.by_operation:
            label = _OPERATION_LABELS.get(op.operation, op.operation)
            lines.append(f"{label}: {op.count} · {op.total:.2f} RUB")
    return "\n".join(lines)


@dataclass
class JobState:
    """Состояние фоновой джобы синхронизации (см. SyncScheduler._jobs)."""

    job_id: str
    status: str = "running"  # running | done | error
    result: Optional[dict] = None
    error: Optional[str] = None
    started_at: float = field(default_factory=time.time)


class SyncScheduler:
    """
    Инкапсулирует состояние синхронизации и предотвращает race conditions.

    Атомарная проверка и установка флага _sync_in_progress внутри lock
    гарантирует, что только одна синхронизация выполняется одновременно.
    """

    def __init__(self, service_data: ServiceDataModel, pool: asyncpg.Pool):
        self._service_data = service_data
        self._pool = pool
        self._sync_lock = asyncio.Lock()
        self._sync_in_progress = False
        self._last_sync_time: Optional[float] = None
        self._sync_count = 0
        self._sweep_lock = asyncio.Lock()
        self._sweep_in_progress = False
        # In-memory реестр запусков admin/sync-jobs: job_id -> JobState.
        self._jobs: Dict[str, "JobState"] = {}
        self._jobs_lock = asyncio.Lock()

    @property
    def is_sync_in_progress(self) -> bool:
        """Thread-safe доступ к флагу синхронизации."""
        return self._sync_in_progress

    @property
    def stats(self) -> Dict[str, Any]:
        """Статистика синхронизаций."""
        return {
            "is_sync_in_progress": self._sync_in_progress,
            "last_sync_time": self._last_sync_time,
            "sync_count": self._sync_count,
        }

    async def sync_cache(self) -> Dict[str, Any]:
        """
        Синхронизирует кэш с БД и панелью.

        Race condition fix: проверка _sync_in_progress внутри lock,
        а не до него. Это гарантирует атомарность проверки и установки.

        Returns:
            Dict со статусом синхронизации и подробной статистикой
            (включая результат панельной синхронизации, чтобы caller'ы
            не вызывали _sync_panel повторно).
        """
        # Атомарная проверка и захват блокировки
        async with self._sync_lock:
            if self._sync_in_progress:
                logger.warning("Синхронизация уже запущена — пропуск дублирующегося запуска")
                return {"status": "skipped", "reason": "sync_already_in_progress"}

            self._sync_in_progress = True
            self._sync_count += 1

        # Выполняем синхронизацию вне lock (но флаг остается True)
        sync_start = time.time()
        panel_stats: Dict[str, Any] = {}
        try:
            from database.service import DataService
            from services.cache.loader import LoadingService

            data_service = DataService()
            loader = LoadingService(
                cache=self._service_data.cache_service,
                data_service=data_service,
                pool=self._pool,
            )
            cache_load_start = time.time()
            await loader.loading()
            cache_load_time = time.time() - cache_load_start
            logger.info("Синхронизация кеша завершена", cache_load_time=f"{cache_load_time:.2f}s")

            # Запускаем синхронизацию с панелью после загрузки кэша
            logger.info("Запуск синхронизации с панелью (отложенный)")
            panel_sync_result = await self._sync_panel()
            # Reconcile: удаляем orphan keys — ключи, чей tg_id больше нет в
            # users (остаются после admin_delete_user с неудачным XUI-удалением).
            orphan_stats = await self._sweep_orphan_keys()
            total_time = time.time() - sync_start
            self._last_sync_time = total_time

            # Прокидываем детальную статистику панели в результат,
            # чтобы итоговое сообщение было наполнено данными.
            panel_stats = dict(panel_sync_result)
            panel_stats["orphan_keys_found"] = orphan_stats.get("orphan_keys_found", 0)
            panel_stats.setdefault("cache_load_time", f"{cache_load_time:.2f}s")
            panel_stats.setdefault("total_time", f"{total_time:.2f}s")
            panel_stats.pop("sync_time", None)
            panel_stats["sync_time"] = f"{total_time:.2f}s"

            logger.info(
                "Полная синхронизация (кэш + панель) завершена",
                total_time=f"{total_time:.2f}s",
                cache_load_time=f"{cache_load_time:.2f}s",
                panel_sync_time=f"{total_time:.2f}s",
            )

            return {
                "status": "success",
                "message": "Cache synchronized",
                "total_time": f"{total_time:.2f}s",
                "cache_load_time": f"{cache_load_time:.2f}s",
                "panel": panel_stats,
            }
        except Exception as e:
            logger.error("Ошибка синхронизации кеша", error=str(e), exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "panel": panel_stats,
            }
        finally:
            # Сбрасываем флаг внутри lock для атомарности
            async with self._sync_lock:
                self._sync_in_progress = False

    async def run_sync_job(self, job_id: str) -> None:
        """Обёртка над sync_cache: обновляет JobState по завершении.

        Запускается как asyncio.Task из start_job. sync_cache сама управляет
        _sync_in_progress (выставляет в lock, сбрасывает в finally) — здесь мы
        только отражаем результат в _jobs.
        """
        try:
            result = await self.sync_cache()
            status = "done" if (result or {}).get("status") != "error" else "error"
            await self._set_job(job_id, status=status, result=result)
        except Exception as e:
            await self._set_job(job_id, status="error", error=str(e))

    async def _set_job(
        self,
        job_id: str,
        status: str,
        result: Optional[dict] = None,
        error: Optional[str] = None,
    ) -> None:
        """Обновляет JobState; prune последних 20 завершённых."""
        async with self._jobs_lock:
            js = self._jobs.get(job_id)
            if js is None:
                js = JobState(job_id=job_id)
                self._jobs[job_id] = js
            js.status = status
            js.result = result
            js.error = error
            done = [j for j in self._jobs.values() if j.status in ("done", "error")]
            if len(done) > 20:
                for j in done[:-20]:
                    self._jobs.pop(j.job_id, None)

    async def get_job(self, job_id: str) -> Optional[JobState]:
        async with self._jobs_lock:
            return self._jobs.get(job_id)

    async def start_job(self) -> tuple[Optional[str], Optional[str]]:
        """Запуск фоновой sync_cache как asyncio.Task.

        Returns:
            (job_id, None) — джоба запущена.
            (None, existing_job_id) — синхронизация уже идёт.

        Координация: атомарная проверка под _sync_lock. Маркер «идёт» — это
        либо ``_sync_in_progress`` (выставляет sync_cache), либо зарегистрированная
        running ``JobState`` (выставляется здесь и в ``run_scheduled_sync``
        немедленно, до того как sync_cache успеет поставить ``_sync_in_progress``).
        Проверка ОБОИХ закрывает race: два одновременных start_job сериализуются
        lock'ом, второй видит running JobState первого → 409, а не второй 202.
        А проверка JobState (а не только флага) делает scheduled-запуски видимыми
        admin/sync-jobs с их настоящим job_id (см. run_scheduled_sync).
        """
        async with self._sync_lock:
            running = next(
                (j for j in self._jobs.values() if j.status == "running"),
                None,
            )
            if self._sync_in_progress or running is not None:
                return None, (running.job_id if running else "unknown")
            job_id = uuid.uuid4().hex
            self._jobs[job_id] = JobState(job_id=job_id, status="running")
            # _sync_in_progress выставит сама sync_cache() внутри своего lock.
            asyncio.create_task(self.run_sync_job(job_id))
        return job_id, None

    async def run_scheduled_sync(self) -> None:
        """Точка входа APScheduler: регистрирует JobState для scheduled-запуска.

        Без этого scheduled sync_cache бежит без JobState → в окне scheduled-запуска
        ``start_job`` не находил running-джобу и отдавал ``"unknown"`` → admin не
        мог опросить статус (GET /admin/sync-jobs/unknown → 404). Регистрируем running
        JobState тем же паттерном, что ``start_job``, затем запускаем sync_cache
        через ``run_sync_job``. Дублирующий scheduled запуск (если coalesce
        пропустил) пропускается.
        """
        async with self._sync_lock:
            running = next(
                (j for j in self._jobs.values() if j.status == "running"),
                None,
            )
            if self._sync_in_progress or running is not None:
                logger.info("scheduled sync: уже идёт — пропуск дублирующегося запуска")
                return
            job_id = uuid.uuid4().hex
            self._jobs[job_id] = JobState(job_id=job_id, status="running")
        await self.run_sync_job(job_id)

    async def _sync_panel(self) -> Dict[str, Any]:
        """Sync panel clients with DB+cache and clean up orphaned keys."""
        from client import XUISession
        from services.cache.loader import LoadingService
        from services.synchron.database_synchronizer import DatabaseSynchronizer
        from services.synchron.xui_fetcher import XUIFetcher
        from services.synchron.cache_comparator import CacheComparator
        from services.synchron.key_creator import KeyCreator
        from services.synchron.tariff_matcher import TariffMatcher
        from services.synchron.traffic import TrafficUpdater
        from database.service import DataService
        import time

        sync_start = time.time()
        logger.info("Начало синхронизации с панелью")
        try:
            data_service = DataService()
            loader = LoadingService(
                cache=self._service_data.cache_service,
                data_service=data_service,
                pool=self._pool,
            )
            session_init_start = time.time()
            xui = XUISession(model_service=self._service_data, loading=loader)
            session_init_time = time.time() - session_init_start

            synchronizer = DatabaseSynchronizer(
                xui_fetcher=XUIFetcher(),
                cache_comparator=CacheComparator(),
                key_creator=KeyCreator(self._service_data, self._pool, TariffMatcher(self._service_data)),
                traffic_updater=TrafficUpdater(self._service_data),
                model_data=self._service_data,
                pool=self._pool,
            )
            async with synchronizer:
                stats = await synchronizer.sync_data(xui_session=xui)
                total_time = time.time() - sync_start
                stats["sync_time"] = f"{total_time:.2f}s"
                logger.info(
                    "Синхронизация с панелью завершена",
                    session_init_time=f"{session_init_time:.2f}s",
                    total_time=f"{total_time:.2f}s",
                    **stats
                )
                return {"status": "success", "sync_time": f"{total_time:.2f}s", **stats}
        except Exception as e:
            logger.error("Ошибка синхронизации с панелью", error=str(e), exc_info=True)
            return {"status": "error", "error": str(e)}

    async def _sweep_orphan_keys(self) -> dict:
        """Обнаруживает orphan keys — ключи, чей tg_id больше нет в users.

        Возникают после admin_delete_user с частично неудачным XUI-удалением:
        пользователь удалён, а key row остался (panel delete упал/вернул False).
        Панель/БД/кэш НЕ трогает — только диагностика и логирование; чистит
        такие ключи админ вручную через admin_delete_key/admin_delete_user.

        Returns:
            ``{"orphan_keys_found": N}`` — число найденных orphan-строк.
        """
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT k.email "
                    "FROM keys k LEFT JOIN users u ON k.tg_id = u.tg_id "
                    "WHERE u.tg_id IS NULL"
                )
            if not rows:
                return {"orphan_keys_found": 0}

            emails = [r["email"] for r in rows]
            logger.warning(
                "orphan_sweep: найдены orphan-ключи, требуют ручного удаления админом",
                extra={"count": len(emails), "emails": emails},
            )
            return {"orphan_keys_found": len(emails)}
        except Exception as e:
            logger.error("orphan_sweep: ошибка", error=str(e), exc_info=True)
            return {"orphan_keys_found": 0}

    async def run_notifications(self) -> None:
        """Run notification funnels cycle."""
        from services.notifications.manager import FunnelManager
        from services.notifications.funnels.key_expiry_24h import KeyExpiryFunnel24h
        from services.notifications.funnels.key_expiry_10h import KeyExpiryFunnel10h
        from services.notifications.funnels.key_expired_grace import KeyExpiredGraceFunnel
        from services.notifications.funnels.trial_reminder import TrialReminderFunnel
        from services.notifications.funnels.cold_lead import ColdLeadFunnel
        from services.notifications.funnels.referral_bonus import ReferralBonusFunnel
        from services.notifications.funnels.referral_reminder import ReferralReminderFunnel
        from services.notifications.funnels.channel_bonus_reminder import ChannelBonusReminderFunnel
        from services.notifications.rate_limiter import RateLimiter

        logger.info("Запуск цикла уведомлений")
        try:
            rate_limiter = RateLimiter()
            funnel_manager = FunnelManager(service_data=self._service_data)
            funnel_manager.register(KeyExpiryFunnel24h(pool=self._pool, rate_limiter=rate_limiter))
            funnel_manager.register(KeyExpiryFunnel10h(pool=self._pool, rate_limiter=rate_limiter))
            funnel_manager.register(KeyExpiredGraceFunnel(pool=self._pool, rate_limiter=rate_limiter))
            funnel_manager.register(TrialReminderFunnel(pool=self._pool, rate_limiter=rate_limiter))
            funnel_manager.register(ColdLeadFunnel(pool=self._pool, rate_limiter=rate_limiter))
            funnel_manager.register(ReferralBonusFunnel(pool=self._pool, rate_limiter=rate_limiter))
            funnel_manager.register(ReferralReminderFunnel(pool=self._pool, rate_limiter=rate_limiter))
            funnel_manager.register(ChannelBonusReminderFunnel(pool=self._pool, rate_limiter=rate_limiter))

            report = await funnel_manager.run_cycle()
            logger.info(
                "Цикл уведомлений завершён",
                users=report.total_users,
                keys_segmented=report.total_keys_segmented,
                duration=f"{report.duration_seconds:.1f}s",
                results=report.results_by_funnel,
            )
        except Exception as e:
            logger.error("Ошибка в цикле уведомлений", error=str(e), exc_info=True)

    async def run_pending_payments_sweep(self) -> None:
        """Safety net для пропущенных webhook'ов YooKassa.

        Опрашивает YooKassa по свежим pending-платежам (младше
        PAYMENT_SWEEP_MAX_AGE_MINUTES) и маршрутизирует succeeded через
        payment_router. exclude-список PAYMENT_SWEEP_EXCLUDE_IDS — payment_id,
        которые уже обработаны вручную (чтобы не продлить ключ повторно).
        Исторический backlog (старше окна) этим джобом НЕ трогается —
        его разбирают разовым скриптом scripts/sweep_pending_payments.py.
        """
        from config import settings
        from services.core.payment.pending_sweep import sweep_pending_payments

        async with self._sweep_lock:
            if self._sweep_in_progress:
                logger.warning("Sweep pending payments уже запущен — пропуск")
                return
            self._sweep_in_progress = True

        try:
            from config import PAYMENT_SWEEP_EXCLUDE_IDS

            await sweep_pending_payments(
                self._pool,
                self._service_data,
                self._service_data.cache_service,
                dry_run=False,
                max_age_minutes=settings.payment_sweep_max_age_minutes,
                exclude_ids=set(PAYMENT_SWEEP_EXCLUDE_IDS),
            )
        except Exception as e:
            logger.error("Ошибка в sweep pending payments", error=str(e), exc_info=True)
        finally:
            async with self._sweep_lock:
                self._sweep_in_progress = False

    async def run_daily_payments_report(self) -> None:
        """Сводка успешных платежей за последние 24 часа, отправка админам из ADMIN_ID.

        Окно скользящее: [now - 24h, now), где now — момент запуска джобы.
        Нулевой день тоже отправляется (сообщение с нулями).
        """
        from config import settings, ADMIN_ID
        from services.core.notifications import TelegramBotNotifier
        from services.core.payment.daily_report import get_daily_report

        if not settings.daily_payments_report_enabled:
            return

        tz = ZoneInfo(settings.daily_payments_report_tz)
        period_end = datetime.now(tz)
        period_start = period_end - timedelta(hours=24)
        try:
            report = await get_daily_report(self._pool, period_start, period_end)
        except Exception as e:
            logger.error("Ошибка сборки ежедневной сводки платежей", error=str(e), exc_info=True)
            return

        text = _format_daily_payments_report(report, tz)
        notifier = TelegramBotNotifier(
            bot_token=settings.bot_token,
            support_chat_url=settings.support_chat_url,
        )
        recipients = [admin_id for admin_id in ADMIN_ID if admin_id]  # 0 — заглушка по умолчанию
        for admin_id in recipients:
            try:
                await notifier.send_message(admin_id, text)
            except Exception as e:
                logger.error("Не удалось отправить сводку платежей админу", admin_id=admin_id, error=str(e))

        logger.info(
            "Ежедневная сводка платежей отправлена",
            payments=report.count,
            total=f"{report.total:.2f}",
            recipients=len(recipients),
        )


def create_scheduler(
    service_data: ServiceDataModel,
    pool: asyncpg.Pool,
) -> AsyncIOScheduler:
    """
    Создает и настраивает APScheduler с использованием SyncScheduler.

    SyncScheduler инкапсулирует состояние и предотвращает race conditions,
    гарантируя атомарность проверки и выполнения синхронизации.
    """
    scheduler = AsyncIOScheduler()

    # Создаем SyncScheduler — инкапсулирует состояние и логику
    sync_scheduler = SyncScheduler(service_data=service_data, pool=pool)

    # Сохраняем в scheduler для доступа извне (например, для admin endpoint)
    scheduler.sync_scheduler = sync_scheduler  # type: ignore

    # Кэш загружается при старте приложения (в lifespan)
    # Планировщик только обновляет его каждые 3 часа.
    # Через run_scheduled_sync (а не sync_cache напрямую), чтобы scheduled-запуск
    # регистрировал JobState — тогда admin/sync-jobs в окне scheduled-запуска отдаёт
    # настоящий job_id для опроса статуса, а не "unknown".
    scheduler.add_job(
        sync_scheduler.run_scheduled_sync,
        "interval",
        hours=3,
        id="sync_cache",
        replace_existing=True,
        coalesce=True,  # Объединять пропущенные запуски
    )

    # Уведомления каждые 1 час
    scheduler.add_job(
        sync_scheduler.run_notifications,
        "interval",
        hours=1,
        id="notifications",
        replace_existing=True,
    )

    # Safety net для пропущенных webhook'ов YooKassa: опрос pending-платежей
    # и маршрутизация succeeded. Каждые 15 минут, только свежие платежи
    # (младше PAYMENT_SWEEP_MAX_AGE_MINUTES), exclude — PAYMENT_SWEEP_EXCLUDE_IDS.
    scheduler.add_job(
        sync_scheduler.run_pending_payments_sweep,
        "interval",
        minutes=15,
        id="pending_payments_sweep",
        replace_existing=True,
        coalesce=True,
    )

    # Ежедневная сводка успешных платежей (по умолчанию 18:00 МСК).
    from config import settings

    if settings.daily_payments_report_enabled:
        report_tz = ZoneInfo(settings.daily_payments_report_tz)
        scheduler.add_job(
            sync_scheduler.run_daily_payments_report,
            CronTrigger(hour=settings.daily_payments_report_hour, minute=0, timezone=report_tz),
            id="daily_payments_report",
            replace_existing=True,
            misfire_grace_time=3600,
            coalesce=True,
        )
    return scheduler
