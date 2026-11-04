"""Фоновая синхронизация inbound-набора клиентов с утверждённым списком.

Реестр задач живёт в памяти процесса (backend запускается одним процессом
uvicorn), как и ``BulkRenewalJobs``: после рестарта задача теряется, но
повторный запуск безопасен — ``set_inbounds`` идемпотентен.
"""
import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Sequence

from client import XUIAPIError
from logger import logger
from services.admin_audit import AuditLogger
from services.core.keys.utils.renewal import renewal_lock

# Сколько клиентов синхронизируются параллельно (у каждого до 3 HTTP-вызовов к панели).
_CONCURRENCY = 5
# Сколько ошибок хранить в состоянии задачи, чтобы ответ админки не разрастался.
_MAX_ERRORS = 20
# Сколько завершённых задач держать в памяти.
_KEEP_FINISHED = 20


async def sync_one_client(
    xui,
    email: str,
    target_inbound_ids: Sequence[int],
    *,
    verify: bool = True,
) -> None:
    """Привести inbound-набор одного клиента к target под локом ключа.

    Лок тот же, что у продления и платежей: синхронизация не должна
    перемешиваться с ``extend_client_key`` / ``set_inbounds`` того же ключа.
    """
    async with renewal_lock(email):
        ok = await xui.set_inbounds(
            email, list(target_inbound_ids), verify=verify
        )
    if not ok:
        raise XUIAPIError(f"Не удалось обновить inbound-набор {email}")


@dataclass
class InboundSyncJobState:
    """Состояние одной фоновой задачи (отдаётся админке через ``as_dict``)."""

    job_id: str
    target_inbound_ids: List[int]
    admin_tg_id: Optional[int]
    total_clients: int
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
            "target_inbound_ids": list(self.target_inbound_ids),
            "total_clients": self.total_clients,
            "processed": self.processed,
            "success": self.success,
            "failed": self.failed,
            "cancelled": self.cancel_requested,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "errors": list(self.errors),
        }


class InboundSyncJobs:
    """Реестр задач синхронизации inbound'ов в этом процессе."""

    def __init__(self, sync_one=None) -> None:
        self._sync_one = sync_one
        self._jobs: dict[str, InboundSyncJobState] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}

    def start(
        self,
        emails: Sequence[str],
        target_inbound_ids: Sequence[int],
        admin_tg_id: Optional[int] = None,
        *,
        xui=None,
        pool=None,
        verify: bool = True,
    ) -> tuple[Optional[str], Optional[str]]:
        """Запустить задачу. Возвращает (job_id, None) или (None, running_job_id).

        Проверка «уже идёт» и вставка состояния идут без await — в рамках
        одного event loop это атомарно, лок не нужен. ``target`` — снимок:
        approve/revoke во время задачи его не меняют.
        """
        running = self._running_job_id()
        if running is not None:
            return None, running

        job_id = uuid.uuid4().hex
        target = [int(i) for i in target_inbound_ids]
        state = InboundSyncJobState(
            job_id=job_id,
            target_inbound_ids=target,
            admin_tg_id=admin_tg_id,
            total_clients=len(emails),
        )
        cancel_event = asyncio.Event()
        self._jobs[job_id] = state
        self._cancel_events[job_id] = cancel_event
        task = asyncio.create_task(
            self._run(
                state,
                list(emails),
                cancel_event,
                xui=xui,
                pool=pool,
                verify=verify,
            ),
            name=f"inbound-sync-{job_id}",
        )
        self._tasks[job_id] = task
        self._prune()
        return job_id, None

    async def get(self, job_id: str) -> Optional[InboundSyncJobState]:
        return self._jobs.get(job_id)

    async def cancel(self, job_id: str) -> Optional[InboundSyncJobState]:
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
        finished = [
            job_id for job_id, state in self._jobs.items() if state.status != "running"
        ]
        for job_id in finished[: max(0, len(finished) - _KEEP_FINISHED)]:
            self._jobs.pop(job_id, None)
            self._tasks.pop(job_id, None)
            self._cancel_events.pop(job_id, None)

    def _resolve_sync(self):
        return self._sync_one if self._sync_one is not None else sync_one_client

    async def _run(self, state, emails, cancel_event, *, xui, pool, verify) -> None:
        sync = self._resolve_sync()
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
                    await sync(
                        xui, email, state.target_inbound_ids, verify=verify
                    )
                    state.success += 1
                except Exception as e:
                    state.failed += 1
                    if len(state.errors) < _MAX_ERRORS:
                        state.errors.append({"email": email, "error": str(e)})
                    logger.error(
                        "Inbound sync failed for client",
                        email=email,
                        error=str(e),
                        job_id=state.job_id,
                    )
                finally:
                    state.processed += 1

        workers = [
            asyncio.create_task(worker(), name=f"inbound-sync-worker-{state.job_id}")
            for _ in range(max(1, min(_CONCURRENCY, len(emails))))
        ]
        try:
            await asyncio.gather(*workers)
            state.status = "cancelled" if cancel_event.is_set() else "done"
        except asyncio.CancelledError:
            # Остановка процесса/реестра: in-flight клиенты могли уже
            # переехать в панели — как при падении процесса, повтор безопасен.
            state.status = "cancelled"
            raise
        except Exception as e:
            state.status = "error"
            logger.error(
                "Inbound sync job failed",
                job_id=state.job_id,
                error_type=type(e).__name__,
                error=str(e),
            )
        finally:
            state.finished_at = datetime.now(timezone.utc)
            await self._audit_finish(state, pool)
            self._prune()

    async def _audit_finish(self, state: InboundSyncJobState, pool) -> None:
        if pool is None:
            return
        try:
            await AuditLogger(pool).record(
                state.admin_tg_id,
                "inbound_sync_finish",
                f"{state.job_id}: {state.status} {state.success}/{state.total_clients}",
            )
        except Exception:
            # AuditLogger сам fail-open; здесь только страховка от сбоя конструкции.
            logger.debug("Inbound sync audit failed", job_id=state.job_id)
