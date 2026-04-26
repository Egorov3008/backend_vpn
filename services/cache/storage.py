from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import asyncio
from models import CacheItem
from services.cache.key_manager import CacheKeyManager
from services.metrics.registry import cache_expired_evictions_total


class CacheStorage:
    def __init__(self, cleanup_interval: timedelta = timedelta(minutes=5)):
        self._storage: Dict[str, Dict[str, CacheItem]] = {}
        self._cleanup_interval = cleanup_interval
        self._cleanup_task: Optional[asyncio.Task] = None

    async def start(self):
        """Старт цикла очистки."""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop(self):
        """Стоп цикла очистки."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

    async def _cleanup_loop(self):
        """Основной цикл очистки."""
        while True:
            await asyncio.sleep(self._cleanup_interval.total_seconds())
            await self._remove_expired()

    async def _remove_expired(self):
        """Удаление просроченных элементов."""
        now = datetime.now()
        for namespace in list(self._storage.keys()):
            expired_keys = [
                key
                for key, item in self._storage[namespace].items()
                if item.expires_at and item.expires_at <= now
            ]
            if expired_keys:
                cache_expired_evictions_total.labels(namespace=namespace).inc(
                    len(expired_keys)
                )
            for key in expired_keys:
                del self._storage[namespace][key]

    async def set(
        self, namespace: str, key: str, value: Any, ttl: Optional[timedelta] = None
    ):
        """Сохранение элемента в хранилище."""
        if namespace not in self._storage:
            self._storage[namespace] = {}
        expires_at = datetime.now() + ttl if ttl else None
        self._storage[namespace][key] = CacheItem(value, expires_at)

    async def get(self, namespace: str, key: str) -> Optional[Any]:
        """Получение элемента из хранилища."""
        ns = self._storage.get(namespace)
        if not ns:
            return None
        item = ns.get(key)
        if not item:
            return None
        if item.expires_at and item.expires_at <= datetime.now():
            await self.delete(namespace, key)
            return None
        return item.value

    async def delete(self, namespace: str, key: str):
        """Удаление элемента из хранилища."""
        if namespace in self._storage and key in self._storage[namespace]:
            del self._storage[namespace][key]

    async def keys(self, namespace: str) -> list:
        """Получение списка ключей в пространстве имен."""
        if namespace not in self._storage:
            return []
        return list(self._storage[namespace].keys())

    async def all_values(self, namespace: str) -> list:
        """Получение всех значений в пространстве имен (исключая временные ключи)."""
        if namespace not in self._storage:
            return []

        result = []
        for key, item in self._storage[namespace].items():
            is_temp = CacheKeyManager.is_temporary(key)
            if item.value and not is_temp:
                result.append(item.value)

        return result
