"""
Сервис для работы с сегментацией ключей.

Предоставляет удобный интерфейс для фильтрации и анализа ключей
по различным критериям (истечение, активность, использование и т.д.)
"""

from typing import List, Dict

from models import Key
from services.core.segmentation.key_ruls import KeySegmenter
from services.core.segmentation.key_manager import KeySegmentationManager
from services.core.segmentation.key_model import KeySegment
from logger import logger


class KeySegmentationService:
    """
    Сервис для сегментации и анализа ключей.

    Использует KeySegmenter для определения сегментов ключей и
    KeySegmentationManager для распределения по сегментам.
    """

    def __init__(self):
        self.segmenter = KeySegmenter()
        self.manager = KeySegmentationManager(self.segmenter)

    async def segment_keys(self, keys: List[Key]) -> Dict[KeySegment, List[Key]]:
        """
        Распределить ключи по сегментам.

        Args:
            keys: Список ключей для распределения

        Returns:
            Словарь {сегмент: список ключей}
        """
        await self.manager.distribution_process(keys)
        return self.manager.get_all_segments()

    async def get_expiring_24h(self, keys: List[Key]) -> List[Key]:
        """Получить ключи, истекающие в ближайшие 24 часа (включая < 10ч).
        
        Фильтрует по времени истечения, независимо от сегмента.
        """
        from datetime import datetime
        now_ms = int(datetime.now().timestamp() * 1000)
        threshold_24h = now_ms + 24 * 3600 * 1000
        
        return [
            key for key in keys
            if now_ms < key.expiry_time <= threshold_24h
        ]

    async def get_expired(self, keys: List[Key]) -> List[Key]:
        """Получить истёкшие ключи."""
        from datetime import datetime
        now_ms = int(datetime.now().timestamp() * 1000)
        return [key for key in keys if key.expiry_time < now_ms]

    async def get_active(self, keys: List[Key]) -> List[Key]:
        """Получить активные ключи."""
        return await self.segmenter.filter_keys(keys, KeySegment.ACTIVE)

    async def get_trial(self, keys: List[Key]) -> List[Key]:
        """Получить trial ключи."""
        return await self.segmenter.filter_keys(keys, KeySegment.TRIAL)

    async def get_unused(self, keys: List[Key]) -> List[Key]:
        """Получить неиспользуемые ключи (0 Гб трафика)."""
        return await self.segmenter.filter_keys(keys, KeySegment.UNUSED)

    async def get_expiring_7d(self, keys: List[Key]) -> List[Key]:
        """Получить ключи, истекающие в ближайшие 7 дней.
        
        Фильтрует по времени истечения, независимо от сегмента.
        """
        from datetime import datetime
        now_ms = int(datetime.now().timestamp() * 1000)
        threshold_7d = now_ms + 7 * 24 * 3600 * 1000
        
        return [
            key for key in keys
            if now_ms < key.expiry_time <= threshold_7d
        ]

    async def get_expiring_30d(self, keys: List[Key]) -> List[Key]:
        """Получить ключи, истекающие в ближайшие 30 дней.
        
        Фильтрует по времени истечения, независимо от сегмента.
        """
        from datetime import datetime
        now_ms = int(datetime.now().timestamp() * 1000)
        threshold_30d = now_ms + 30 * 24 * 3600 * 1000
        
        return [
            key for key in keys
            if now_ms < key.expiry_time <= threshold_30d
        ]

    def get_segment_stats(self) -> Dict[str, int]:
        """Получить статистику по сегментам."""
        return self.manager.get_all_counts()

    async def filter_by_name(self, keys: List[Key], name: str) -> List[Key]:
        """
        Фильтровать ключи по названию сегмента.

        Args:
            keys: Список ключей
            name: Название сегмента (название из KeySegment enum)

        Returns:
            Отфильтрованные ключи
        """
        try:
            segment = KeySegment(name)
            return await self.segmenter.filter_keys(keys, segment)
        except ValueError:
            logger.warning("Неизвестный сегмент", segment_name=name)
            return []
