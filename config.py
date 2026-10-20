import ast
import os
from ipaddress import ip_network
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.config import core_settings, REFERRAL_BONUS_PERCENTAGES  # noqa: F401
from shared.config.core import reject_insecure_secret


def _parse_list(raw: str | None, default: list | None = None) -> list:
    if raw is None:
        return default or []
    try:
        result = ast.literal_eval(raw)
        return result if isinstance(result, list) else [result]
    except (ValueError, SyntaxError):
        return default or []


# .env lives next to config.py at the repo root.
_env_file = Path(__file__).parent / ".env"

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_env_file), extra="ignore", populate_by_name=True)

    # Core — sourced from shared.config.core_settings where possible
    database_url: str = ""
    bot_secret_key: str = "changeme"
    # Single-use registration token for web form registration invites
    invite_token: str = "changeme"
    admin_api_key: str = "changeme"
    log_level: str = "INFO"
    log_file: str = ""
    log_format: str = "detailed"

    # 3x-UI panel
    api_url: str = Field(default="", alias="XUI_API_URL")
    xui_subscription_url: str = Field(default="", alias="XUI_SUB")
    external_subscription_url: str = Field(default="", alias="EXTERNAL_SUB_URL")
    admin_username: str = Field(default="", alias="XUI_LOGIN")
    admin_password: str = Field(default="", alias="XUI_PASSWORD")
    xui_server_id: int = Field(default=1, alias="XUI_SERVER_ID")
    xui_skip_ssl_verify: bool = Field(default=False, alias="XUI_SKIP_SSL_VERIFY")

    # MVP shared-config endpoint
    mvp_app_secret: str = Field(default="changeme", alias="MVP_APP_SECRET")
    mvp_shared_key_email: str = Field(default="", alias="MVP_SHARED_KEY_EMAIL")

    @field_validator("bot_secret_key", "invite_token", "admin_api_key", "mvp_app_secret", "miniapp_session_secret")
    @classmethod
    def _validate_secret(cls, value: str, info) -> str:
        return reject_insecure_secret(info.field_name.upper(), value)

    # Landing page
    # Separate 3x-UI inbound for anonymous 24h keys. Landing-only — NOT part
    # of a paid/trial key's inbound set (see paid_inbound_ids()), so it does
    # not need to be in AVAILABLE_CONNECTIONS.
    xui_inbound_id_landing: int = Field(default=0, alias="XUI_INBOUND_ID_LANDING")
    # HMAC secret for signed landing cookies. Falls back to bot_secret_key if empty.
    landing_cookie_secret: str = Field(default="", alias="LANDING_COOKIE_SECRET")
    # Public URL of the landing page (used in deep-link to bot).
    landing_public_url: str = Field(default="", alias="LANDING_PUBLIC_URL")
    # Second secret for bot-only landing routes (claim, mark-converted). nginx
    # injects X-Bot-Secret on the public /api/v1/landing/* paths, so that header
    # alone must not be enough. Empty = these routes are closed.
    landing_internal_secret: str = Field(default="", alias="LANDING_INTERNAL_SECRET")

    # Telegram Mini App (/api/v1/miniapp). HMAC secret for session tokens.
    # Required: placeholder/empty values are rejected at startup (no fallback).
    miniapp_session_secret: str = Field(default="changeme", alias="MINIAPP_SESSION_SECRET")
    miniapp_session_ttl_seconds: int = Field(default=3600, alias="MINIAPP_SESSION_TTL_SECONDS")
    # Max age of Telegram.WebApp.initData accepted by POST /miniapp/auth.
    miniapp_init_data_max_age_seconds: int = Field(default=86400, alias="MINIAPP_INIT_DATA_MAX_AGE_SECONDS")
    # Public URL of the Mini App frontend. When set, the "renew key" button in
    # expiry notifications opens its pay screen as a WebApp; empty keeps the bot callback.
    miniapp_url: str = Field(default="", alias="MINIAPP_URL")

    @field_validator("miniapp_url")
    @classmethod
    def _validate_miniapp_url(cls, value: str) -> str:
        from shared.miniapp_url import normalize_miniapp_url

        return normalize_miniapp_url(value)

    # Web cabinet (/api/v1/web). Session tokens are signed with
    # MINIAPP_SESSION_SECRET under a separate label (app/core/miniapp_session.py).
    web_session_ttl_seconds: int = Field(default=7 * 24 * 3600, alias="WEB_SESSION_TTL_SECONDS")
    # Lifetime of a "log in via the bot" token (deep link + polling).
    web_login_token_ttl_seconds: int = Field(default=600, alias="WEB_LOGIN_TOKEN_TTL_SECONDS")
    # Public URL of the web cabinet, e.g. https://app.example.ru. Used as the
    # YooKassa return_url for payments made from the web. Empty = back to the bot.
    web_public_url: str = Field(default="", alias="WEB_PUBLIC_URL")
    # Secure flag on web cookies. Turn off only for local http development.
    web_cookie_secure: bool = Field(default=True, alias="WEB_COOKIE_SECURE")

    # Web admin (Mini App of a separate admin bot). Its initData is signed with
    # ADMIN_BOT_TOKEN; only tg_id from ADMIN_ID gets a session. Empty = admin
    # login is closed (the bot keeps working with X-API-Key).
    admin_bot_token: str = Field(default="", alias="ADMIN_BOT_TOKEN")
    admin_session_ttl_seconds: int = Field(default=8 * 3600, alias="ADMIN_SESSION_TTL_SECONDS")
    # Max age of the admin bot's initData accepted by POST /admin/session.
    admin_init_data_max_age_seconds: int = Field(default=3600, alias="ADMIN_INIT_DATA_MAX_AGE_SECONDS")

    # YooKassa — values fall back to shared core_settings if .env is missing them
    yookassa_shop_id: str = ""
    yookassa_secret_key: str = ""
    disable_webhook_ip_check: bool = False
    receipt_customer_email: str = ""  # Fiscal receipt email (required for Russian fiscal law)

    # Telegram
    bot_token: str = ""
    # Username бота берётся у Telegram (getMe по BOT_TOKEN); BOT_NAME — только
    # запасной вариант, если getMe недоступен. Никакого вшитого имени.
    bot_name: str = ""
    support_chat_url: str = ""

    # Bot behaviour
    admin_id_raw: str = Field(default="[0]", alias="ADMIN_ID")
    available_rates_raw: str = Field(default="[9, 8, 7]", alias="AVAILABLE_RATES")
    available_connections_raw: str = Field(default="[11, 12]", alias="AVAILABLE_CONNECTIONS")
    default_pricing_plan: str = "10"
    trial_time: int = 30
    discounts: int = 3
    limit_ip: int = 3

    # Monitoring
    metrics_port: int = 9101
    webhook_path: str = "/api/v1/payments/webhook"
    webhook_base_url: str = ""  # Public URL of this backend, e.g. https://api.example.com

    # Окно «свежих» pending-платежей. Фонового опроса ЮKassa нет (ключ выдаётся только
    # по вебхуку); окно используют create_payment (поиск незавершённого счёта за ту же
    # покупку) и ручной scripts/sweep_pending_payments.py по умолчанию.
    # payment_sweep_exclude_ids — payment_id, которые ручной sweep пропускает
    # (напр. уже обработанные вручную админом без обновления статуса — чтобы не продлить ключ повторно).
    payment_sweep_max_age_minutes: int = Field(default=1440, alias="PAYMENT_SWEEP_MAX_AGE_MINUTES")
    payment_sweep_exclude_ids_raw: str = Field(default="", alias="PAYMENT_SWEEP_EXCLUDE_IDS")

    # Promo: +N days for subscribing to Telegram channel
    channel_bonus_days: int = Field(default=7, alias="CHANNEL_BONUS_DAYS")
    # Public URL of the Telegram channel (same CANALL_URL the bot uses).
    # Backend needs it to call getChatMember for subscription checks.
    canall_url: str = Field(default="", alias="CANALL_URL")

    # Ежедневная сводка успешных платежей за последние 24 часа — админам из ADMIN_ID.
    # Время отправки — час по DAILY_PAYMENTS_REPORT_TZ (минуты фиксированы: 0).
    daily_payments_report_enabled: bool = Field(default=True, alias="DAILY_PAYMENTS_REPORT_ENABLED")
    daily_payments_report_hour: int = Field(default=18, ge=0, le=23, alias="DAILY_PAYMENTS_REPORT_HOUR")
    daily_payments_report_tz: str = Field(default="Europe/Moscow", alias="DAILY_PAYMENTS_REPORT_TZ")

    # Прокси, которым верим X-Forwarded-For/X-Real-IP (app/client_ip.py):
    # список CIDR через запятую. По умолчанию loopback и частные сети docker.
    trusted_proxy_cidrs_raw: str = Field(
        default="127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7",
        alias="TRUSTED_PROXY_CIDRS",
    )

    @property
    def trusted_proxy_cidrs(self) -> list[str]:
        return [c.strip() for c in self.trusted_proxy_cidrs_raw.split(",") if c.strip()]

    @field_validator("trusted_proxy_cidrs_raw")
    @classmethod
    def _validate_trusted_proxies(cls, value: str) -> str:
        for cidr in (c.strip() for c in value.split(",")):
            if cidr:
                ip_network(cidr, strict=False)  # ValueError на опечатке — падаем при старте
        return value

    @field_validator("daily_payments_report_tz")
    @classmethod
    def _validate_report_tz(cls, value: str) -> str:
        # Неверная зона иначе роняет create_scheduler и весь старт приложения
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"DAILY_PAYMENTS_REPORT_TZ: неизвестная часовая зона {value!r}") from e
        return value


settings = Settings()

# ============================================================
# Module-level compat vars — used by copied bot modules via
# `from config import DATABASE_URL` pattern (flat imports)
# Source priority: env-loaded Settings > shared.core_settings
# ============================================================
DATABASE_URL: str = settings.database_url or core_settings.database_url
API_URL: str = settings.api_url
XUI_SUBSCRIPTION_URL: str = settings.xui_subscription_url or settings.api_url
EXTERNAL_SUBSCRIPTION_URL: str = settings.external_subscription_url
ADMIN_USERNAME: str = settings.admin_username
ADMIN_PASSWORD: str = settings.admin_password
XUI_SERVER_ID: int = settings.xui_server_id
XUI_SKIP_SSL_VERIFY: bool = settings.xui_skip_ssl_verify
YOOKASSA_SHOP_ID: str = settings.yookassa_shop_id or core_settings.yookassa_shop_id
YOOKASSA_SECRET_KEY: str = settings.yookassa_secret_key or core_settings.yookassa_secret_key
DISABLE_WEBHOOK_IP_CHECK: bool = settings.disable_webhook_ip_check
BOT_TOKEN: str = settings.bot_token
BOT_NAME: str = settings.bot_name
SUPPORT_CHAT_URL: str = settings.support_chat_url
DEFAULT_PRICING_PLAN: str = settings.default_pricing_plan
TRIAL_TIME: int = settings.trial_time
DISCOUNTS: int = settings.discounts
LIMIT_IP: int = settings.limit_ip
METRICS_PORT: int = settings.metrics_port
WEBHOOK_PATH: str = settings.webhook_path
PAYMENT_SWEEP_MAX_AGE_MINUTES: int = settings.payment_sweep_max_age_minutes
PAYMENT_SWEEP_EXCLUDE_IDS: list = [
    s.strip() for s in settings.payment_sweep_exclude_ids_raw.split(",") if s.strip()
]

ADMIN_ID: list = _parse_list(settings.admin_id_raw, [0])
AVAILABLE_RATES_LIST: list = _parse_list(settings.available_rates_raw, [])
LIST_AVAILABLE_CONNECTIONS: list = _parse_list(settings.available_connections_raw, [])

# Referral bonus percentages — single source of truth in shared.config
REFERRAL_BONUS_PERCENTAGES: dict = REFERRAL_BONUS_PERCENTAGES

# Referral bonus constants for bonus_service.py
REFERRAL_BONUS_PERCENT: float = 0.10  # 10% bonus for referrer
REFERRAL_DISCOUNT_PERCENT: float = 0.10  # 10% referral discount

# Minimum amount (₽) the user must pay out-of-pocket — we never let the
# balance discount drive the final amount below this floor.
MIN_PAYMENT_AMOUNT: float = float(os.getenv("MIN_PAYMENT_AMOUNT", "10.0"))

# Aliases expected by some bot modules
WEBHOOK_HOST: str = os.getenv("WEBHOOK_HOST", "0.0.0.0")
WEBHOOK_PORT: int = int(os.getenv("WEBHOOK_PORT", "8000"))
