from enum import Enum


class KeySegment(Enum):
    """Сегменты ключей"""

    EXPIRING_10H = "expiring_10h"  # Истекает в ближайшие 10 часов
    EXPIRING_24H = "expiring_24h"  # Истекает в ближайшие 24 часа
    EXPIRING_7D = "expiring_7d"  # Истекает в ближайшие 7 дней
    EXPIRING_30D = "expiring_30d"  # Истекает в ближайшие 30 дней
    EXPIRED = "expired"  # Устаревший ключ
    ACTIVE = "active"  # Активный ключ
    TRIAL = "trial"  # Trial ключ
    UNUSED = "unused"  # Неиспользуемый ключ (0 Гб трафика)
    ALL = "all"  # Все ключи
