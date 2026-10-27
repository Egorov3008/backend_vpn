"""Фоновая отправка рассылки: темп, повторы после 429, отмена, продолжение после рестарта."""
import asyncio
import time
from typing import Awaitable, Callable, Optional

from logger import logger
from services.broadcasts.models import (
    ACTIVE_STATUSES,
    BroadcastMessage,
    BroadcastStatus,
    DeliveryStatus,
    SendResult,
)
from services.broadcasts.repository import BroadcastRepository
from services.broadcasts.telegram import BroadcastBot

Sleep = Callable[[float], Awaitable[None]]


class Pacer:
    """Не больше ``rate`` запросов в секунду (равномерно, без всплесков)."""

    def __init__(self, rate: float, clock: Callable[[], float] = time.monotonic, sleep: Sleep = asyncio.sleep):
        self._interval = 1.0 / rate if rate > 0 else 0.0
        self._clock = clock
        self._sleep = sleep
        self._next = 0.0

    async def wait(self) -> None:
        now = self._clock()
        if self._next > now:
            await self._sleep(self._next - now)
            now = self._next
        self._next = now + self._interval


class BroadcastRunner:
    """Шлёт одну рассылку по снимку получателей.

    Отмена проверяется перед каждой пачкой. Если Telegram не принял токен (401)
    или подряд идут одни ошибки, рассылка останавливается со статусом failed,
    а неотправленные остаются в очереди (видно в прогрессе).
    """

    def __init__(
        self,
        repo: BroadcastRepository,
        bot: BroadcastBot,
        rate_per_second: float,
        batch_size: int = 25,
        max_attempts: int = 5,
        max_failures_in_row: int = 50,
        sleep: Sleep = asyncio.sleep,
    ):
        self._repo = repo
        self._bot = bot
        self._pacer = Pacer(rate_per_second, sleep=sleep)
        self._batch_size = batch_size
        self._max_attempts = max_attempts
        self._max_failures_in_row = max_failures_in_row
        self._sleep = sleep

    async def run(self, broadcast_id: int) -> None:
        broadcast = await self._repo.get(broadcast_id)
        if broadcast is None or broadcast.status.value not in ACTIVE_STATUSES:
            return
        # Взятые, но не отправленные прошлым процессом (рестарт посреди пачки)
        await self._repo.release_claimed(broadcast_id)
        logger.info("Рассылка: старт", broadcast_id=broadcast_id, pending=broadcast.progress.pending)
        try:
            outcome, error = await self._send_all(broadcast_id, broadcast.message)
        except asyncio.CancelledError:
            raise  # остановка процесса: продолжим после рестарта
        except Exception as e:
            logger.error("Рассылка: сбой", broadcast_id=broadcast_id, error_type=type(e).__name__, error=str(e))
            outcome, error = BroadcastStatus.FAILED, f"Внутренняя ошибка: {type(e).__name__}"
        await self._repo.finish(broadcast_id, outcome, error)
        logger.info("Рассылка: конец", broadcast_id=broadcast_id, status=outcome.value, error=error)

    async def _send_all(self, broadcast_id: int, message: BroadcastMessage) -> tuple[BroadcastStatus, Optional[str]]:
        failures_in_row = 0
        while True:
            if await self._repo.status(broadcast_id) != BroadcastStatus.RUNNING:
                return BroadcastStatus.CANCELED, None
            batch = await self._repo.claim(broadcast_id, self._batch_size)
            if not batch:
                return BroadcastStatus.DONE, None
            for i, tg_id in enumerate(batch):
                result = await self._deliver(tg_id, message)
                if result.unauthorized:
                    await self._repo.release_claimed(broadcast_id, batch[i:])
                    return BroadcastStatus.FAILED, f"Telegram не принял токен бота: {result.error}"
                await self._repo.record(broadcast_id, tg_id, result.status, result.error)
                failures_in_row = failures_in_row + 1 if result.status == DeliveryStatus.FAILED else 0
                if failures_in_row >= self._max_failures_in_row:
                    await self._repo.release_claimed(broadcast_id, batch[i + 1:])
                    return BroadcastStatus.FAILED, f"{failures_in_row} ошибок подряд, последняя: {result.error}"

    async def _deliver(self, tg_id: int, message: BroadcastMessage) -> SendResult:
        result = SendResult(DeliveryStatus.FAILED)
        for attempt in range(1, self._max_attempts + 1):
            await self._pacer.wait()
            result = await self._bot.send(tg_id, message)
            if result.retry_after is None or attempt == self._max_attempts:
                break
            await self._sleep(result.retry_after)
        if result.ok and message.pin and result.message_id is not None:
            await self._pacer.wait()
            await self._bot.pin(tg_id, result.message_id)
        return result


RunnerFactory = Callable[[], BroadcastRunner]


class BroadcastJobs:
    """Задачи рассылок в этом процессе (backend работает одним процессом uvicorn)."""

    def __init__(self) -> None:
        self._tasks: dict[int, asyncio.Task] = {}

    def is_running(self, broadcast_id: int) -> bool:
        task = self._tasks.get(broadcast_id)
        return task is not None and not task.done()

    def start(self, broadcast_id: int, runner: BroadcastRunner) -> None:
        if self.is_running(broadcast_id):
            return
        task = asyncio.create_task(runner.run(broadcast_id), name=f"broadcast-{broadcast_id}")
        self._tasks[broadcast_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(broadcast_id, None))

    async def resume(self, repo: BroadcastRepository, make_runner: RunnerFactory) -> list[int]:
        """После старта backend: продолжить незаконченные рассылки."""
        ids = await repo.active_ids()
        for broadcast_id in ids:
            self.start(broadcast_id, make_runner())
        if ids:
            logger.info("Рассылки продолжены после старта", ids=ids)
        return ids

    async def wait(self, broadcast_id: int) -> None:
        task = self._tasks.get(broadcast_id)
        if task is not None:
            await asyncio.shield(task)

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
