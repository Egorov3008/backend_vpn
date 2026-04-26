from typing import List, Dict, Any

from services.core.segmentation.ruls import UserSegmenter


class SegmentationManager:
    def __init__(self, segmentation: UserSegmenter):
        self.segmentation = segmentation
        self._distribution_users = {}

    async def distribution_proces(self, users: List[Dict[str, Any]]) -> Any:
        """Сортировка пользователей по сегментам"""
        for user_data in users:
            user = user_data.get("user")
            segment = await self.segmentation.determine_segment(user_data)
            if self._distribution_users.get(segment):
                self._distribution_users[segment].append(user)
            else:
                self._distribution_users[segment] = [user]
        return self._distribution_users
