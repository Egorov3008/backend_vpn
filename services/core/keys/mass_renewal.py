"""
Сервис массового продления ключей.

Позволяет продлить множество ключей на указанное количество дней
с обновлением XUI панели, БД и кеша.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

import asyncpg

from client import XUISession
from logger import logger
from models import Key
from services.cache.key_manager import CacheKeyManager
from services.cache.service import CacheService
from services.core.keys.utils.reset import KeyResetter


@dataclass
class RenewalResult:
    """Результат продления одного ключа."""
    email: str
    success: bool
    old_expiry_ms: int
    new_expiry_ms: int
    error: Optional[str] = None


@dataclass
class MassRenewalReport:
    """Общий отчёт о массовом продлении."""
    total: int = 0
    success: int = 0
    failed: int = 0
    results: List[RenewalResult] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return (self.success / self.total) * 100

    def format_summary(self) -> str:
        """Форматированная сводка для отправки админу."""
        lines = [
            f"📦 <b>Массовое продление завершено</b>",
            f"",
            f"📊 Всего ключей: <b>{self.total}</b>",
            f"✅ Успешно: <b>{self.success}</b>",
            f"❌ Ошибки: <b>{self.failed}</b>",
            f"📈 Процент успеха: <b>{self.success_rate:.1f}%</b>",
        ]
        return "\n".join(lines)

    def format_details(self, max_details: int = 10) -> str:
        """Подробный отчёт с первыми N успешными ключами."""
        lines = [self.format_summary(), ""]

        success_results = [r for r in self.results if r.success][:max_details]
        if success_results:
            lines.append("✅ <b>Успешно продлены:</b>")
            for r in success_results:
                old_dt = datetime.fromtimestamp(
                    r.old_expiry_ms / 1000, tz=timezone.utc
                ).strftime("%d.%m.%Y")
                new_dt = datetime.fromtimestamp(
                    r.new_expiry_ms / 1000, tz=timezone.utc
                ).strftime("%d.%m.%Y")
                lines.append(f"  <code>{r.email}</code>: {old_dt} → {new_dt}")

            if len([r for r in self.results if r.success]) > max_details:
                lines.append(
                    f"  ... и ещё {len([r for r in self.results if r.success]) - max_details} ключей"
                )

        failed_results = [r for r in self.results if not r.success][:max_details]
        if failed_results:
            lines.append("")
            lines.append("❌ <b>Ошибки:</b>")
            for r in failed_results:
                lines.append(f"  <code>{r.email}</code>: {r.error}")

            if len([r for r in self.results if not r.success]) > max_details:
                lines.append(
                    f"  ... и ещё {len([r for r in self.results if not r.success]) - max_details} ошибок"
                )

        return "\n".join(lines)


class MassKeyRenewal:
    """
    Сервис массового продления ключей.

    Продлевает список ключей на указанное количество дней,
    обновляя XUI панель, БД и кеш для каждого ключа.
    """

    def __init__(
        self,
        xui_session: XUISession,
        cache: CacheService,
        resetter: KeyResetter,
    ):
        self.xui_session = xui_session
        self.cache = cache
        self.resetter = resetter

    async def renew_keys(
        self,
        pool: asyncpg.Pool,
        keys: List[Key],
        days: int,
    ) -> MassRenewalReport:
        """
        Продлить список ключей на указанное количество дней.

        Args:
            pool: Пул соединений БД
            keys: Список ключей для продления
            days: Количество дней для продления

        Returns:
            MassRenewalReport с результатами по каждому ключу
        """
        report = MassRenewalReport(total=len(keys))
        semaphore = asyncio.Semaphore(10)  # Ограничение параллельных операций

        async def _renew_single(key: Key) -> RenewalResult:
            async with semaphore:
                old_expiry_ms = key.expiry_time

                try:
                    # Вычисляем новую дату истечения
                    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
                    base_expiry = max(old_expiry_ms, now_ms)
                    new_expiry_ms = base_expiry + (days * 24 * 3600 * 1000)
                    key.expiry_time = new_expiry_ms

                    # Обновляем параллельно: XUI, БД, кеш
                    results = await asyncio.gather(
                        self.xui_session.extend_client_key(key),
                        self._update_key_in_db(pool, key),
                        self.cache.keys.set(CacheKeyManager.key(key.email), key),
                        return_exceptions=True,
                    )

                    errors = [r for r in results if isinstance(r, Exception)]
                    if errors:
                        raise errors[0]

                    # Сбрасываем флаги уведомлений и трафик
                    await self.resetter.reset_key_after_renewal(pool, key)

                    return RenewalResult(
                        email=key.email,
                        success=True,
                        old_expiry_ms=old_expiry_ms,
                        new_expiry_ms=new_expiry_ms,
                    )

                except Exception as e:
                    logger.error(
                        "Ошибка при массовом продлении ключа",
                        email=key.email,
                        error=str(e),
                        exc_info=True,
                    )
                    return RenewalResult(
                        email=key.email,
                        success=False,
                        old_expiry_ms=old_expiry_ms,
                        new_expiry_ms=old_expiry_ms,
                        error=str(e),
                    )

        # Продлеваем все ключи параллельно
        tasks = [_renew_single(key) for key in keys]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                report.failed += 1
                report.results.append(
                    RenewalResult(
                        email="unknown",
                        success=False,
                        old_expiry_ms=0,
                        new_expiry_ms=0,
                        error=str(result),
                    )
                )
            else:
                report.results.append(result)
                if result.success:
                    report.success += 1
                else:
                    report.failed += 1

        logger.info(
            "Массовое продление завершено",
            total=report.total,
            success=report.success,
            failed=report.failed,
            days=days,
        )

        return report

    async def _update_key_in_db(self, pool: asyncpg.Pool, key: Key) -> None:
        """Обновить ключ в БД."""
        from services.core.data.service import ServiceDataModel
        from services.conteiner.app import get_container

        container = await get_container()
        model_data: ServiceDataModel = container.resolve(ServiceDataModel)
        await model_data.keys.update(pool, key, {"email": key.email})
