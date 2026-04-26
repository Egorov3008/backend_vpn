"""
Реестр всех Prometheus-метрик приложения.

Единая точка определения всех Counter, Gauge, Histogram.
Импортируй нужные метрики напрямую:
    from services.metrics.registry import payment_total, key_created_total
"""

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry()

# ============================================================
# Business: Платежи
# ============================================================

payment_total = Counter(
    "vpn_payment_total",
    "Количество обработанных платежей",
    ["status", "operation"],
    registry=REGISTRY,
)

payment_amount_rub_total = Counter(
    "vpn_payment_amount_rub_total",
    "Суммарная выручка в рублях",
    ["operation"],
    registry=REGISTRY,
)

payment_processing_duration = Histogram(
    "vpn_payment_processing_duration_seconds",
    "Время обработки платежа от вебхука до завершения",
    ["operation"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
    registry=REGISTRY,
)

webhook_requests_total = Counter(
    "vpn_webhook_requests_total",
    "Количество входящих вебхуков",
    ["event_type", "status"],
    registry=REGISTRY,
)

# ============================================================
# Business: Ключи
# ============================================================

key_created_total = Counter(
    "vpn_key_created_total",
    "Количество созданных ключей",
    ["type"],
    registry=REGISTRY,
)

key_renewed_total = Counter(
    "vpn_key_renewed_total",
    "Количество продлённых ключей",
    registry=REGISTRY,
)

key_deleted_total = Counter(
    "vpn_key_deleted_total",
    "Количество удалённых ключей",
    registry=REGISTRY,
)

key_creation_errors_total = Counter(
    "vpn_key_creation_errors_total",
    "Ошибки при создании ключей",
    ["error_type"],
    registry=REGISTRY,
)

keys_by_segment = Gauge(
    "vpn_keys_by_segment",
    "Количество ключей по сегментам",
    ["segment"],
    registry=REGISTRY,
)

# ============================================================
# Business: Регистрация
# ============================================================

user_registered_total = Counter(
    "vpn_user_registered_total",
    "Количество зарегистрированных пользователей",
    ["type"],
    registry=REGISTRY,
)

users_total_count = Gauge(
    "vpn_users_total_count",
    "Общее количество пользователей в кеше",
    registry=REGISTRY,
)

# ============================================================
# Business: Уведомления
# ============================================================

notification_sent_total = Counter(
    "vpn_notification_sent_total",
    "Количество отправленных уведомлений",
    ["funnel_id"],
    registry=REGISTRY,
)

notification_blocked_total = Counter(
    "vpn_notification_blocked_total",
    "Количество блокировок уведомлений",
    ["funnel_id"],
    registry=REGISTRY,
)

notification_error_total = Counter(
    "vpn_notification_error_total",
    "Количество ошибок уведомлений",
    ["funnel_id"],
    registry=REGISTRY,
)

notification_cycle_duration = Histogram(
    "vpn_notification_cycle_duration_seconds",
    "Длительность цикла уведомлений",
    buckets=[1, 5, 10, 30, 60, 120, 300],
    registry=REGISTRY,
)

rate_limiter_tokens = Gauge(
    "vpn_rate_limiter_tokens",
    "Текущее количество токенов rate limiter",
    registry=REGISTRY,
)

# ============================================================
# Infrastructure: Handler latency
# ============================================================

handler_duration = Histogram(
    "vpn_handler_duration_seconds",
    "Время выполнения обработчиков aiogram",
    ["handler", "event_type", "status"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
    registry=REGISTRY,
)

# ============================================================
# Infrastructure: БД
# ============================================================

db_pool_size = Gauge(
    "vpn_db_pool_size",
    "Размер пула соединений БД",
    registry=REGISTRY,
)

db_pool_free = Gauge(
    "vpn_db_pool_free",
    "Свободные соединения в пуле БД",
    registry=REGISTRY,
)

db_pool_used = Gauge(
    "vpn_db_pool_used",
    "Используемые соединения в пуле БД",
    registry=REGISTRY,
)

# ============================================================
# Infrastructure: Кеш
# ============================================================

cache_items_count = Gauge(
    "vpn_cache_items_count",
    "Количество элементов в кеше по namespace",
    ["namespace"],
    registry=REGISTRY,
)

cache_expired_evictions_total = Counter(
    "vpn_cache_expired_evictions_total",
    "Количество удалённых просроченных элементов кеша",
    ["namespace"],
    registry=REGISTRY,
)

# ============================================================
# Infrastructure: XUI API
# ============================================================

xui_api_calls_total = Counter(
    "vpn_xui_api_calls_total",
    "Количество вызовов XUI API",
    ["method"],
    registry=REGISTRY,
)

xui_api_errors_total = Counter(
    "vpn_xui_api_errors_total",
    "Ошибки XUI API",
    ["method", "error_type"],
    registry=REGISTRY,
)

xui_api_duration = Histogram(
    "vpn_xui_api_duration_seconds",
    "Время вызовов XUI API",
    ["method"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0],
    registry=REGISTRY,
)

xui_api_retries_total = Counter(
    "vpn_xui_api_retries_total",
    "Количество повторных попыток XUI API",
    ["method"],
    registry=REGISTRY,
)

# ============================================================
# Infrastructure: Telegram Bot API
# ============================================================

telegram_messages_sent_total = Counter(
    "vpn_telegram_messages_sent_total",
    "Количество отправленных Telegram-сообщений",
    ["result"],
    registry=REGISTRY,
)

telegram_flood_control_total = Counter(
    "vpn_telegram_flood_control_total",
    "Количество срабатываний flood control",
    registry=REGISTRY,
)

# ============================================================
# Background Tasks
# ============================================================

background_sync_last_run = Gauge(
    "vpn_background_sync_last_run_timestamp",
    "Timestamp последнего запуска синхронизации кеша",
    registry=REGISTRY,
)

background_sync_duration = Histogram(
    "vpn_background_sync_duration_seconds",
    "Длительность синхронизации кеша",
    buckets=[1, 5, 10, 30, 60, 120, 300, 600],
    registry=REGISTRY,
)

background_sync_errors_total = Counter(
    "vpn_background_sync_errors_total",
    "Ошибки синхронизации кеша",
    registry=REGISTRY,
)

background_notification_last_run = Gauge(
    "vpn_background_notification_last_run_timestamp",
    "Timestamp последнего запуска цикла уведомлений",
    registry=REGISTRY,
)

# ============================================================
# Errors (общий)
# ============================================================

errors_total = Counter(
    "vpn_errors_total",
    "Общее количество ошибок по слоям",
    ["layer", "error_type"],
    registry=REGISTRY,
)
