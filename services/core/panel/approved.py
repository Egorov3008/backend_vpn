"""Управление утверждённым набором inbound'ов панели 3x-UI.

Источник истины — таблица ``approved_inbounds``; кеш
``CacheService.approved_inbounds`` читается на горячем пути (создание ключа,
продление). Набор наполняется только через админские эндпоинты
(``POST /admin/inbounds/{id}/approve``).
"""
import asyncio
from typing import TYPE_CHECKING, Iterable, List, Optional

from database.service import ApprovedInboundRepository
from logger import logger
from services.cache.key_manager import CacheKeyManager
from services.cache.service import CacheService
from services.core.keys.utils.inbounds import approved_inbound_ids

if TYPE_CHECKING:  # избегаем цикла client -> services.cache -> loader -> panel.approved
    from client import XUISession

# Один лок на процесс: периодический reload кеша и мутации approve/revoke не
# должны перемешиваться (reload делает clear + заполнение).
_approved_reload_lock = asyncio.Lock()


class PanelUnavailable(Exception):
    """Панель 3x-UI недоступна или вернула ошибку."""


class InboundNotOnPanel(Exception):
    """Указанного inbound'а нет в панели."""


class NoApprovedInbounds(Exception):
    """Утверждённый набор пуст — синхронизировать некуда."""


class LastApprovedInbound(Exception):
    """Нельзя отозвать последний утверждённый inbound."""


class InvalidSyncTarget(Exception):
    """Запрошенный набор inbound'ов некорректен."""

    def __init__(
        self,
        message: str,
        *,
        invalid_inbound_ids: Optional[Iterable[int]] = None,
        approved_inbound_ids: Optional[Iterable[int]] = None,
    ) -> None:
        super().__init__(message)
        self.invalid_inbound_ids = list(invalid_inbound_ids or [])
        self.approved_inbound_ids = list(approved_inbound_ids or [])


async def reload_approved_inbounds(
    cache: CacheService,
    repo: ApprovedInboundRepository,
    pool,
) -> List[int]:
    """Атомарно перечитать утверждённый набор из БД в кеш.

    ``clear`` обязателен: LoadingService не чистит namespace'ы, поэтому без него
    отозванные inbound'ы жили бы в кеше до рестарта.
    """
    async with _approved_reload_lock:
        rows = await repo.list_ordered(pool)
        await cache.approved_inbounds.clear()
        for row in rows:
            await cache.approved_inbounds.set(
                CacheKeyManager.approved_inbound(row.inbound_id), row
            )
        return [int(row.inbound_id) for row in rows]


class ApprovedInboundsService:
    """Чтение панели и approve/revoke утверждённого набора."""

    def __init__(self, repo: ApprovedInboundRepository, xui: "XUISession") -> None:
        self.repo = repo
        self.xui = xui

    async def _panel_ids(self) -> set[int]:
        try:
            inbounds = await self.xui.get_inbounds(strict=True)
        except Exception as e:
            logger.error(
                "Не удалось получить inbound'ы панели",
                error_type=type(e).__name__,
                error=str(e),
            )
            raise PanelUnavailable(str(e)) from e
        ids: set[int] = set()
        for raw in inbounds:
            if not isinstance(raw, dict):
                continue
            ib_id = raw.get("id")
            if ib_id is not None:
                try:
                    ids.add(int(ib_id))
                except (TypeError, ValueError):
                    continue
        return ids

    async def list_panel(self, cache: CacheService) -> dict:
        """Inbound'ы панели с флагом ``approved`` + отсутствующие в панели id."""
        try:
            raw_inbounds = await self.xui.get_inbounds(strict=True)
        except Exception as e:
            logger.error(
                "Не удалось получить inbound'ы панели",
                error_type=type(e).__name__,
                error=str(e),
            )
            raise PanelUnavailable(str(e)) from e

        approved = await approved_inbound_ids(cache)
        approved_set = set(approved)
        items: list[dict] = []
        panel_ids: set[int] = set()
        for raw in raw_inbounds:
            if not isinstance(raw, dict):
                continue
            ib_id = raw.get("id")
            if ib_id is None:
                continue
            try:
                ib_id = int(ib_id)
            except (TypeError, ValueError):
                continue
            panel_ids.add(ib_id)
            items.append(
                {
                    "inbound_id": ib_id,
                    "remark": raw.get("remark"),
                    "protocol": raw.get("protocol"),
                    "enable": bool(raw.get("enable", True)),
                    "approved": ib_id in approved_set,
                }
            )
        items.sort(key=lambda item: item["inbound_id"])
        return {
            "inbounds": items,
            "approved_ids": approved,
            "missing_approved_ids": [i for i in approved if i not in panel_ids],
        }

    async def approve(
        self,
        pool,
        cache: CacheService,
        inbound_id: int,
        admin_tg_id: Optional[int],
    ) -> tuple:
        """Утвердить inbound (идемпотентно) и обновить кеш."""
        if inbound_id not in await self._panel_ids():
            raise InboundNotOnPanel(str(inbound_id))
        model, created = await self.repo.add(pool, inbound_id, admin_tg_id)
        await reload_approved_inbounds(cache, self.repo, pool)
        return model, created

    async def revoke(self, pool, cache: CacheService, inbound_id: int) -> bool:
        """Отозвать inbound; последний отозвать нельзя."""
        existing = await self.repo.get(pool, inbound_id=inbound_id)
        if existing is None:
            return False
        if not await self.repo.remove_if_not_last(pool, inbound_id):
            raise LastApprovedInbound(str(inbound_id))
        await reload_approved_inbounds(cache, self.repo, pool)
        return True

    async def resolve_target(
        self, cache: CacheService, requested: Optional[List[int]]
    ) -> List[int]:
        """Проверить и вернуть target: подмножество approved, существующее в панели."""
        approved = await approved_inbound_ids(cache)
        if not approved:
            raise NoApprovedInbounds("No approved inbounds")

        target = self._dedup(requested) if requested is not None else list(approved)
        if not target:
            raise InvalidSyncTarget(
                "Empty inbound target", approved_inbound_ids=approved
            )

        approved_set = set(approved)
        outside = [i for i in target if i not in approved_set]
        if outside:
            raise InvalidSyncTarget(
                "Inbound is not approved",
                invalid_inbound_ids=outside,
                approved_inbound_ids=approved,
            )

        panel_ids = await self._panel_ids()
        missing = [i for i in target if i not in panel_ids]
        if missing:
            raise InvalidSyncTarget(
                "Inbound is not present on panel",
                invalid_inbound_ids=missing,
                approved_inbound_ids=approved,
            )
        return target

    @staticmethod
    def _dedup(ids: Iterable[int]) -> List[int]:
        seen: set[int] = set()
        out: List[int] = []
        for raw in ids:
            try:
                ib_id = int(raw)
            except (TypeError, ValueError):
                continue
            if ib_id not in seen:
                seen.add(ib_id)
                out.append(ib_id)
        return out
