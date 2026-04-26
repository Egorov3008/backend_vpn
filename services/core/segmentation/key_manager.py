from typing import List, Dict

from .key_ruls import KeySegmenter
from .key_model import KeySegment
from models import Key
from logger import logger


class KeySegmentationManager:
    """Менеджер распределения ключей по сегментам."""

    def __init__(self, segmenter: KeySegmenter):
        self.segmenter = segmenter
        self._distribution_keys: Dict[KeySegment, List[Key]] = {}

    async def distribution_process(
        self, keys: List[Key]
    ) -> Dict[KeySegment, List[Key]]:
        """
        Сортировка ключей по сегментам.

        Args:
            keys: Список ключей для распределения

        Returns:
            Словарь с сегментами и соответствующими ключами
        """
        self._distribution_keys = {}

        for key in keys:
            try:
                segment = await self.segmenter.determine_segment(key)

                if segment not in self._distribution_keys:
                    self._distribution_keys[segment] = []

                self._distribution_keys[segment].append(key)

            except Exception as e:
                logger.error(
                    "Ошибка при распределении ключа по сегменту",
                    key_email=key.email,
                    error=str(e),
                    exc_info=True,
                )

        return self._distribution_keys

    def get_segment(self, segment: KeySegment) -> List[Key]:
        """Получить ключи определённого сегмента."""
        return self._distribution_keys.get(segment, [])

    def get_all_segments(self) -> Dict[KeySegment, List[Key]]:
        """Получить всё распределение ключей."""
        return self._distribution_keys

    def get_segment_count(self, segment: KeySegment) -> int:
        """Получить количество ключей в сегменте."""
        return len(self.get_segment(segment))

    def get_all_counts(self) -> Dict[str, int]:
        """Получить подсчет ключей по всем сегментам."""
        return {
            segment.value: len(keys)
            for segment, keys in self._distribution_keys.items()
        }
