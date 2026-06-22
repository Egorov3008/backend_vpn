import ast
import os
import sys
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.config import core_settings, REFERRAL_BONUS_PERCENTAGES  # noqa: F401


def _parse_list(raw: str | None, default: list | None = None) -> list:
    if raw is None:
        return default or []
    try:
        result = ast.literal_eval(raw)
        return result if isinstance(result, list) else [result]
    except (ValueError, SyntaxError):
        return default or []


# Locate .env in project root (accounting for worktree: backend/config.py → need 5 parents to reach /home/claude/vpn-platform/)
_env_path = Path(__file__).parent.parent.parent.parent.parent / ".env"
if not _env_path.exists():
    # Fallback: try worktree root (2 parents)
    _env_path = Path(__file__).parent.parent / ".env"
_env_file = _env_path

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
    admin_username: str = Field(default="", alias="XUI_LOGIN")
    admin_password: str = Field(default="", alias="XUI_PASSWORD")
    xui_web_base_path: str = Field(default="/", alias="XUI_WEB_BASE_PATH")
    xui_server_id: int = Field(default=1, alias="XUI_SERVER_ID")
    xui_skip_ssl_verify: bool = Field(default=False, alias="XUI_SKIP_SSL_VERIFY")

    # Landing page
    # Separate 3x-UI inbound for anonymous 24h keys. Must also be in
    # AVAILABLE_CONNECTIONS so form_data can pick it up.
    xui_inbound_id_landing: int = Field(default=0, alias="XUI_INBOUND_ID_LANDING")
    # HMAC secret for signed landing cookies. Falls back to bot_secret_key if empty.
    landing_cookie_secret: str = Field(default="", alias="LANDING_COOKIE_SECRET")
    # Public URL of the landing page (used in deep-link to bot).
    landing_public_url: str = Field(default="", alias="LANDING_PUBLIC_URL")

    # YooKassa — values fall back to shared core_settings if .env is missing them
    yookassa_shop_id: str = ""
    yookassa_secret_key: str = ""
    disable_webhook_ip_check: bool = False
    receipt_customer_email: str = ""  # Fiscal receipt email (required for Russian fiscal law)

    # Telegram
    bot_token: str = ""
    bot_name: str = "VPNBot"
    support_chat_url: str = ""
    url_bot: str = ""

    # Bot behaviour
    admin_id_raw: str = "[0]"
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

    # Pending-payment sweep (safety net for missed YooKassa webhooks).
    # Авто-поллинг из scheduler опрашивает YooKassa по pending-платежам младше
    # payment_sweep_max_age_minutes и маршрутизирует succeeded (через payment_router).
    # payment_sweep_exclude_ids — список payment_id, которые поллинг пропускает
    # (напр. уже обработанные вручную админом без обновления статуса — чтобы не продлить ключ повторно).
    payment_sweep_max_age_minutes: int = Field(default=1440, alias="PAYMENT_SWEEP_MAX_AGE_MINUTES")
    payment_sweep_exclude_ids_raw: str = Field(default="", alias="PAYMENT_SWEEP_EXCLUDE_IDS")


settings = Settings()

# ============================================================
# Module-level compat vars — used by copied bot modules via
# `from config import DATABASE_URL` pattern (flat imports)
# Source priority: env-loaded Settings > shared.core_settings
# ============================================================
DATABASE_URL: str = settings.database_url or core_settings.database_url
API_URL: str = settings.api_url
XUI_SUBSCRIPTION_URL: str = settings.xui_subscription_url or settings.api_url
ADMIN_USERNAME: str = settings.admin_username
ADMIN_PASSWORD: str = settings.admin_password
XUI_WEB_BASE_PATH: str = settings.xui_web_base_path
XUI_SERVER_ID: int = settings.xui_server_id
XUI_SKIP_SSL_VERIFY: bool = settings.xui_skip_ssl_verify
YOOKASSA_SHOP_ID: str = settings.yookassa_shop_id or core_settings.yookassa_shop_id
YOOKASSA_SECRET_KEY: str = settings.yookassa_secret_key or core_settings.yookassa_secret_key
DISABLE_WEBHOOK_IP_CHECK: bool = settings.disable_webhook_ip_check
BOT_TOKEN: str = settings.bot_token
BOT_NAME: str = settings.bot_name
SUPPORT_CHAT_URL: str = settings.support_chat_url
URL_BOT: str = settings.url_bot
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
REFERRAL_BONUS_DAYS: int = 3  # +3 days for referred user
REFERRAL_DISCOUNT_PERCENT: float = 0.10  # 10% referral discount

# Minimum amount (₽) the user must pay out-of-pocket — we never let the
# balance discount drive the final amount below this floor.
MIN_PAYMENT_AMOUNT: float = float(os.getenv("MIN_PAYMENT_AMOUNT", "10.0"))

# Aliases expected by some bot modules
WEBHOOK_HOST: str = os.getenv("WEBHOOK_HOST", "0.0.0.0")
WEBHOOK_PORT: int = int(os.getenv("WEBHOOK_PORT", "8000"))
