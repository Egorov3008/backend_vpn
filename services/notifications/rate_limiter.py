"""Rate limiter для отправки Telegram сообщений из backend scheduler."""

import asyncio
import time
from typing import Optional

from logger import logger

_GLOBAL_RATE = 25  # msg/sec
_PER_USER_DELAY = 1.1  # sec


class RateLimiter:
    """Token-bucket rate limiter (memory-only, используется внутри одного цикла)."""

    def __init__(self, global_rate: float = _GLOBAL_RATE, per_user_delay: float = _PER_USER_DELAY):
        self._global_rate = global_rate
        self._per_user_delay = per_user_delay
        self._tokens: float = global_rate
        self._last_refill: float = time.monotonic()
        self._last_sent: dict[int, float] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, tg_id: int) -> None:
        """Дождаться, пока можно отправить сообщение."""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._global_rate, self._tokens + elapsed * self._global_rate)
            self._last_refill = now

            wait_user = max(0.0, self._per_user_delay - (now - self._last_sent.get(tg_id, 0.0)))
            wait_global = 0.0 if self._tokens >= 1 else (1 - self._tokens) / self._global_rate
            wait = max(wait_user, wait_global)

            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(self._global_rate, self._tokens + elapsed * self._global_rate)
                self._last_refill = now

            self._tokens -= 1
            self._last_sent[tg_id] = time.monotonic()
