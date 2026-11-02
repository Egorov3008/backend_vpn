"""Массовое продление ключей на N дней: одна операция и фоновый реестр задач.

Продление одного ключа (``renew_key_by_days``) переиспользуется синхронным
админским эндпоинтом и фоновой задачей. Реестр ``BulkRenewalJobs`` живёт в
памяти процесса (backend запускается одним процессом uvicorn): после рестарта
задача теряется, повторный запуск продлит ключи ещё раз — поэтому перед
запуском обязателен preview.
"""
import asyncio
import copy
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable, List, Optional

import asyncpg

from client import XUIAPIError
from logger import logger
from services.admin_audit import AuditLogger
from services.core.data.service import ServiceDataModel
from services.core.keys.utils.inbounds import paid_inbound_ids
from services.core.keys.utils.renewal import renewal_lock
from services.core.keys.utils.reset import KeyResetter

DAY_MS = 86_400_000

# Сколько задач продления выполняются параллельно (у каждой 2 HTTP-вызова к панели).
_CONCURRENCY = 5
# Сколько ошибок хранить в состоянии задачи, чтобы ответ админки не разрастался.
_MAX_ERRORS = 20
# Сколько завершённых задач держать в памяти.
_KEEP_FINISHED = 20


class KeyNotFoundError(Exception):
    """Ключ не найден в БД (или удалён, пока задача ждала лок)."""


RenewOne = Callable[..., Awaitable[int]]


async def renew_key_by_days(
    pool: asyncpg.Pool,
    service_data: ServiceDataModel,
    cache,
    xui,
    email: str,
    days: int,
) -> int:
    """Продлить ключ на ``days`` дней. Возвращает новый expiry_time (ms).

    Единый путь продления для старого mass-renew и bulk-задачи:
    - читает ключ из БД под процессным локом (авторитетный срок, защита от
      удалённого ключа и параллельного продления);
    - панель обновляется ДО БД: если панель не приняла срок, в БД/кеше ничего
      не меняется и ключ попадает в ошибки задачи.
    """
    key = await service_data.keys.get_data(email)
    if key is None:
        key = await service_data.data_service.keys.get(pool, email=email)
    if key is None:
        raise KeyNotFoundError(email)

    async with renewal_lock(email):
        # Кэш-first здесь не годится: пока ждали лок, ключ мог измениться или
        # быть удалён; срок берём из БД.
        current = await service_data.data_service.keys.get(pool, email=email)
        if current is None:
            raise KeyNotFoundError(email)

        now_ms = int(time.time() * 1000)
        renewed = copy.copy(current)
        renewed.expiry_time = max(renewed.expiry_time, now_ms) + days * DAY_MS

        inbound_ok = await xui.set_inbounds(renewed.email, paid_inbound_ids())
        if not inbound_ok:
            logger.warning(
                "Не удалось синхронизировать inbound-набор перед продлением; продолжаем best-effort",
                email=renewed.email,
                operation="bulk_renew",
            )
        # extend_client_key возвращает False, а не бросает исключение.
        if not await xui.extend_client_key(renewed):
            raise XUIAPIError(f"Не удалось продлить ключ {renewed.email} в панели 3x-ui")

        await service_data.keys.update(pool, renewed, {"email": renewed.email})
        await KeyResetter(cache_service=cache).reset_key_after_renewal(pool, renewed)
        return renewed.expiry_time


@dataclass
class BulkRenewalJobState:
    """Состояние одной фоновой задачи (отдаётся админке через ``as_dict``)."""

    job_id: str
    days: int
    admin_tg_id: Optional[int]
    total_keys: int
    status: str = "running"  # running | done | cancelled | error
    processed: int = 0
    success: int = 0
    failed: int = 0
    errors: List[dict] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None
    cancel_requested: bool = False

    def as_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "days": self.days,
            "total_keys": self.total_keys,
            "processed": self.processed,
            "success": self.success,
            "failed": self.failed,
            "cancelled": self.cancel_requested,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "errors": list(self.errors),
        }


class BulkRenewalJobs:
    """Реестр задач массового продления в этом процессе.

    ``renew_one`` подменяет функцию продления (unit-тесты). Если не задана,
    функция ищется в модуле в момент запуска — это позволяет интеграционным
    тестам подменить ``renew_key_by_days`` через monkeypatch.
    """

    def __init__(self, renew_one: Optional[RenewOne] = None) -> None:
        self._renew_one = renew_one
        self._jobs: dict[str, BulkRenewalJobState] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}

    async def start(
        self,
        emails: List[str],
        days: int,
        admin_tg_id: Optional[int],
        *,
        pool=None,
        service_data=None,
        cache=None,
        xui=None,
    ) -> tuple[Optional[str], Optional[str]]:
        """Запустить задачу. Возвращает (job_id, None) или (None, running_job_id).

        Проверка «уже идёт» и создание состояния идут без await — в рамках
        одного event loop это атомарно, лок не нужен.
        """
        running = self._running_job_id()
        if running is not None:
            return None, running

        job_id = uuid.uuid4().hex
        state = BulkRenewalJobState(
            job_id=job_id, days=days, admin_tg_id=admin_tg_id, total_keys=len(emails)
        )
        cancel_event = asyncio.Event()
        self._jobs[job_id] = state
        self._cancel_events[job_id] = cancel_event
        task = asyncio.create_task(
            self._run(
                state,
                list(emails),
                cancel_event,
                pool=pool,
                service_data=service_data,
                cache=cache,
                xui=xui,
            ),
            name=f"bulk-renew-{job_id}",
        )
        self._tasks[job_id] = task
        self._prune()
        return job_id, None

    async def get(self, job_id: str) -> Optional[BulkRenewalJobState]:
        return self._jobs.get(job_id)

    async def cancel(self, job_id: str) -> Optional[BulkRenewalJobState]:
        """Попросить остановку. Возвращает состояние или None, если задачи нет."""
        state = self._jobs.get(job_id)
        if state is None:
            return None
        if state.status == "running":
            state.cancel_requested = True
            event = self._cancel_events.get(job_id)
            if event is not None:
                event.set()
        return state

    async def wait(self, job_id: str) -> None:
        """Дождаться завершения задачи (интеграционные тесты)."""
        task = self._tasks.get(job_id)
        if task is not None:
            await asyncio.shield(task)

    async def shutdown(self) -> None:
        tasks = [t for t in self._tasks.values() if not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._cancel_events.clear()

    async def reset(self) -> None:
        """Погасить задачи и очистить реестр (интеграционный стенд)."""
        await self.shutdown()
        self._jobs.clear()

    def _running_job_id(self) -> Optional[str]:
        for job_id, state in self._jobs.items():
            if state.status == "running":
                return job_id
        return None

    def _prune(self) -> None:
        finished = [jid for jid, state in self._jobs.items() if state.status != "running"]
        for job_id in finished[: max(0, len(finished) - _KEEP_FINISHED)]:
            self._jobs.pop(job_id, None)
            self._tasks.pop(job_id, None)
            self._cancel_events.pop(job_id, None)

    def _resolve_renew(self) -> RenewOne:
        return self._renew_one if self._renew_one is not None else renew_key_by_days

    async def _run(self, state, emails, cancel_event, *, pool, service_data, cache, xui) -> None:
        renew = self._resolve_renew()
        queue: asyncio.Queue[str] = asyncio.Queue()
        for email in emails:
            queue.put_nowait(email)

        async def worker() -> None:
            while not cancel_event.is_set():
                try:
                    email = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    await renew(pool, service_data, cache, xui, email, state.days)
                    state.success += 1
                except Exception as e:
                    state.failed += 1
                    if len(state.errors) < _MAX_ERRORS:
                        state.errors.append({"email": email, "error": str(e)})
                    logger.error(
                        "Bulk renew failed for key",
                        email=email,
                        error=str(e),
                        job_id=state.job_id,
                    )
                finally:
                    state.processed += 1

        workers = [
            asyncio.create_task(worker(), name=f"bulk-renew-worker-{state.job_id}")
            for _ in range(max(1, min(_CONCURRENCY, len(emails))))
        ]
        try:
            await asyncio.gather(*workers)
            state.status = "cancelled" if cancel_event.is_set() else "done"
        except asyncio.CancelledError:
            # Остановка процесса/реестра: in-flight ключи могли успеть обновить
            # панель, но не БД — как при падении процесса.
            state.status = "cancelled"
            raise
        except Exception as e:
            state.status = "error"
            logger.error(
                "Bulk renewal job failed", job_id=state.job_id, error_type=type(e).__name__, error=str(e)
            )
        finally:
            state.finished_at = datetime.now(timezone.utc)
            await self._audit_finish(state, pool)
            self._prune()

    async def _audit_finish(self, state: BulkRenewalJobState, pool) -> None:
        if pool is None:
            return
        try:
            await AuditLogger(pool).record(
                state.admin_tg_id,
                "bulk_renew_finish",
                f"{state.job_id}: {state.status} {state.success}/{state.total_keys}",
            )
        except Exception:
            # AuditLogger сам fail-open; здесь только страховка от сбоя конструкции.
            logger.debug("Bulk renewal audit failed", job_id=state.job_id)
