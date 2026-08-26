"""Простой in-memory rate limiting для публично торчащих эндпоинтов
(mobile-mvp, landing, webhook YooKassa) — единственных путей, куда
nginx пускает трафик напрямую из интернета (см. nginx/default.conf.template).

Sliding window per-IP, без внешних зависимостей. Ограничение: состояние не
разделяется между процессами/репликами backend — при текущем однопроцессном
деплое это не проблема; при горизонтальном масштабировании потребует
переноса в Redis.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple

from fastapi import HTTPException, Request

from app.client_ip import extract_client_ip

_buckets: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)


def rate_limit(key: str, times: int, seconds: int):
    """Возвращает FastAPI-зависимость: не более `times` запросов за `seconds`
    секунд с одного IP, отдельно по каждому `key` (обычно — имя эндпоинта)."""

    async def _dependency(request: Request) -> None:
        ip = extract_client_ip(request) or "unknown"
        bucket_key = (key, ip)
        now = time.monotonic()
        window_start = now - seconds

        bucket = _buckets[bucket_key]
        while bucket and bucket[0] < window_start:
            bucket.popleft()

        if len(bucket) >= times:
            raise HTTPException(status_code=429, detail="Too many requests")

        bucket.append(now)
        if len(_buckets) > 10_000:
            # Защита от неограниченного роста словаря по числу уникальных
            # IP — на практике не должно срабатывать при нормальном трафике.
            for stale_key in [k for k, v in _buckets.items() if not v]:
                _buckets.pop(stale_key, None)

    return _dependency
