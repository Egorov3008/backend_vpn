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

ADMIN_ID: list = _parse_list(settings.admin_id_raw, [0])
AVAILABLE_RATES_LIST: list = _parse_list(settings.available_rates_raw, [])
LIST_AVAILABLE_CONNECTIONS: list = _parse_list(settings.available_connections_raw, [])

# Referral bonus percentages — single source of truth in shared.config
REFERRAL_BONUS_PERCENTAGES: dict = REFERRAL_BONUS_PERCENTAGES

# Aliases expected by some bot modules
WEBHOOK_HOST: str = os.getenv("WEBHOOK_HOST", "0.0.0.0")
WEBHOOK_PORT: int = int(os.getenv("WEBHOOK_PORT", "8000"))
