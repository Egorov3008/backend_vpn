"""Запись событий: проверка по каталогу и запись в хранилище.

Сбой записи никогда не ломает действие пользователя: ошибка логируется и глотается.
"""

from typing import Any, Mapping, Optional

from logger import logger
from services.core.payment.admin_notification import PaidPayment
from services.user_events.catalog import SOURCES, EventCatalog
from services.user_events.repository import EventSink, UserEvent


class EventTracker:
    def __init__(self, sink: EventSink, catalog: EventCatalog, enabled: bool = True):
        self._sink = sink
        self._catalog = catalog
        self._enabled = enabled

    async def track(
        self,
        name: str,
        *,
        source: str,
        tg_id: Optional[int] = None,
        anon_id: Optional[str] = None,
        props: Optional[Mapping[str, Any]] = None,
    ) -> None:
        if not self._enabled:
            return
        event_type = self._catalog.get(name)
        if event_type is None or source not in SOURCES:
            logger.warning("user_events: событие не из каталога", event=name, source=source)
            return
        if tg_id is None and anon_id is None:
            return
        try:
            await self._sink.write(
                UserEvent(name=name, source=source, tg_id=tg_id, anon_id=anon_id, props=event_type.clean(props))
            )
        except Exception as e:
            logger.error("user_events: не удалось записать событие", event=name, error=str(e))

    async def track_payment_created(
        self,
        *,
        source: str,
        tg_id: int,
        operation: str,
        tariff_id: int,
        months: int,
        amount: float,
        promo_code: Optional[str] = None,
    ) -> None:
        """Счёт выставлен (оплачен он или нет — видно по payment_succeeded)."""
        await self.track(
            "payment_created",
            source=source,
            tg_id=tg_id,
            props={
                "operation": operation,
                "tariff_id": tariff_id,
                "months": months,
                "amount": amount,
                "promo": promo_code,
            },
        )


class PaymentEventListener:
    """``PaymentSuccessListener``: успешная оплата попадает в журнал как payment_succeeded."""

    def __init__(self, tracker: EventTracker):
        self._tracker = tracker

    async def on_payment_succeeded(self, payment: PaidPayment) -> None:
        await self._tracker.track(
            "payment_succeeded",
            source="backend",
            tg_id=payment.tg_id,
            props={"operation": payment.operation, "amount": payment.amount},
        )
