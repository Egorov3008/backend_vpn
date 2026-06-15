"""FunnelManager — оркестратор воронок уведомлений (backend scheduler)."""

import time
from datetime import datetime, timezone, timedelta
from typing import List

from logger import logger
from models.keys.key import Key
from models.users.user import User
from services.cache.service import CacheService
from services.core.data.service import ServiceDataModel
from services.notifications.models import NotificationContext, FunnelRunReport
from services.notifications.rate_limiter import RateLimiter
from services.notifications.routing import SEGMENT_BY_FUNNEL, SENDING_HOUR_WINDOW
from services.notifications.segmentation import KeySegmenter

_MOSCOW_TZ = timezone(timedelta(hours=3))


class FunnelManager:
    """Оркестратор воронок уведомлений (backend)."""

    def __init__(self, service_data: ServiceDataModel) -> None:
        self._service_data = service_data
        self._funnels: list = []
        self._rate_limiter = RateLimiter()
        self._segmenter = KeySegmenter()

    def register(self, funnel) -> None:
        """Зарегистрировать воронку."""
        self._funnels.append(funnel)
        logger.debug("Зарегистрирована воронка", funnel_id=funnel.funnel_id)

    async def run_cycle(self) -> FunnelRunReport:
        """Запустить один цикл уведомлений по всем воронкам."""
        report = FunnelRunReport()
        t0 = time.monotonic()

        if not self._in_sending_window():
            moscow_now = datetime.now(_MOSCOW_TZ)
            logger.info(
                "Уведомления: нерабочее время, пропуск цикла",
                moscow_hour=moscow_now.hour,
                window=list(SENDING_HOUR_WINDOW),
            )
            return report

        # Load users and keys from cache (backend is source of truth)
        users: list[User] = await self._service_data.users.get_all()
        all_keys: list[Key] = await self._service_data.keys.get_all()

        keys_by_user: dict[int, list[Key]] = {}
        for key in all_keys:
            keys_by_user.setdefault(key.tg_id, []).append(key)

        report.total_users = len(users)
        logger.info(
            "Запуск цикла уведомлений",
            users=len(users),
            funnels=len(self._funnels),
        )

        for user in users:
            if user.is_blocked:
                continue

            user_keys = keys_by_user.get(user.tg_id, [])

            for funnel in self._funnels:
                try:
                    segment_keys = self._get_segment_keys(funnel.funnel_id, user_keys)
                    ctx = NotificationContext(
                        user=user,
                        keys=user_keys,
                        segment_keys=segment_keys,
                    )

                    if not await funnel.should_send(ctx):
                        continue

                    result = await funnel.process(ctx)
                    report.add_result(result)
                    report.total_keys_segmented += len(segment_keys)

                except Exception as exc:
                    # exc_info=True attaches the full traceback so we can
                    # diagnose the funnel failure from errors.log without
                    # having to reproduce the cycle.
                    logger.error(
                        "Ошибка в воронке",
                        funnel_id=funnel.funnel_id,
                        tg_id=user.tg_id,
                        error_type=type(exc).__name__,
                        error=str(exc),
                        exc_info=True,
                    )

        report.duration_seconds = time.monotonic() - t0
        if report.total_keys_segmented == 0:
            logger.warning(
                "Цикл уведомлений: ни один ключ не попал ни в один сегмент",
                users=report.total_users,
                blocked=sum(1 for u in users if u.is_blocked),
                keys=len(all_keys),
            )
        logger.info(
            "Цикл уведомлений завершён",
            users=report.total_users,
            keys_segmented=report.total_keys_segmented,
            duration=f"{report.duration_seconds:.1f}s",
            results=report.results_by_funnel,
        )
        return report

    def _get_segment_keys(self, funnel_id: str, user_keys: list[Key]) -> list[Key]:
        """Получить ключи для воронки (для ключевых воронок — по сегменту; иначе [])."""
        segment = SEGMENT_BY_FUNNEL.get(funnel_id)
        if segment is None or not user_keys:
            return []
        try:
            return self._segmenter.filter_keys(user_keys, segment)
        except Exception as e:
            logger.error(
                "Ошибка при фильтрации ключей",
                funnel_id=funnel_id,
                segment=segment.value,
                error=str(e),
            )
            return []

    @staticmethod
    def _in_sending_window() -> bool:
        start, end = SENDING_HOUR_WINDOW
        moscow_now = datetime.now(_MOSCOW_TZ)
        return start <= moscow_now.hour < end
