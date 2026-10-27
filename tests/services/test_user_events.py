"""EventTracker: в журнал попадает только то, что разрешено каталогом, и сбой записи не мешает пользователю."""
from services.user_events import EventCatalog, EventTracker, EventType


class _Sink:
    def __init__(self, fail: bool = False):
        self.events = []
        self.fail = fail

    async def write(self, event) -> None:
        if self.fail:
            raise RuntimeError("db down")
        self.events.append(event)


CATALOG = EventCatalog([EventType("payment_created", "Создал счёт", frozenset({"amount", "tariff_id"}))])


async def test_unknown_event_or_source_is_dropped():
    sink = _Sink()
    tracker = EventTracker(sink, CATALOG)

    await tracker.track("drop_table", source="web", tg_id=1)
    await tracker.track("payment_created", source="evil", tg_id=1)

    assert sink.events == []


async def test_only_allowed_simple_props_are_kept():
    sink = _Sink()
    tracker = EventTracker(sink, CATALOG)

    await tracker.track(
        "payment_created",
        source="web",
        tg_id=1,
        props={"amount": 150.0, "tariff_id": "x" * 500, "ip": "1.2.3.4", "nested": {"a": 1}},
    )

    [event] = sink.events
    assert event.props["amount"] == 150.0
    assert len(event.props["tariff_id"]) < 500
    assert "ip" not in event.props and "nested" not in event.props


async def test_disabled_tracker_writes_nothing():
    sink = _Sink()

    await EventTracker(sink, CATALOG, enabled=False).track("payment_created", source="web", tg_id=1)

    assert sink.events == []


async def test_event_without_actor_is_dropped():
    sink = _Sink()

    await EventTracker(sink, CATALOG).track("payment_created", source="web")

    assert sink.events == []


async def test_sink_failure_does_not_raise():
    await EventTracker(_Sink(fail=True), CATALOG).track("payment_created", source="web", tg_id=1)
