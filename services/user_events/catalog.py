"""Каталог событий: какие имена и какие поля props разрешено записывать.

Новое событие = новая запись в ``DEFAULT_EVENT_TYPES``; трекер и хранилище не меняются.
Всё, чего нет в каталоге (чужое имя, лишнее поле, длинная строка), отбрасывается:
журнал не должен превращаться в склад произвольных данных от клиентов.
"""

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional

# Откуда пришло событие. backend — само событие сервера (оплата по вебхуку и т. п.).
SOURCES = frozenset({"miniapp", "web", "renew_link", "backend"})

MAX_PROP_LENGTH = 64


@dataclass(frozen=True)
class EventType:
    """Разрешённое событие: имя, русская подпись для админки и допустимые поля props."""

    name: str
    title: str
    props: frozenset[str] = frozenset()

    def clean(self, props: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
        """Только разрешённые поля и только простые значения; строки обрезаются."""
        result: Dict[str, Any] = {}
        for key, value in (props or {}).items():
            if key not in self.props or value is None:
                continue
            if isinstance(value, bool) or isinstance(value, (int, float)):
                result[key] = value
            elif isinstance(value, str):
                result[key] = value[:MAX_PROP_LENGTH]
        return result


DEFAULT_EVENT_TYPES = (
    EventType("app_opened", "Открыл приложение"),
    EventType("web_login", "Вошёл на сайт через бота"),
    EventType("trial_created", "Взял пробный ключ"),
    EventType("payment_created", "Создал счёт", frozenset({"operation", "tariff_id", "months", "amount", "promo"})),
    EventType("payment_succeeded", "Оплатил", frozenset({"operation", "amount"})),
    EventType("key_deleted", "Удалил ключ"),
)


class EventCatalog:
    """Поиск типа события по имени."""

    def __init__(self, types: Iterable[EventType] = DEFAULT_EVENT_TYPES):
        self._types = {t.name: t for t in types}

    def get(self, name: str) -> Optional[EventType]:
        return self._types.get(name)

    def titles(self) -> Dict[str, str]:
        return {name: t.title for name, t in self._types.items()}
