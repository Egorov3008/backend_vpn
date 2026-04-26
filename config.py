import ast
import os
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_list(raw: str | None, default: list | None = None) -> list:
    if raw is None:
        return default or []
    try:
        result = ast.literal_eval(raw)
        return result if isinstance(result, list) else [result]
    except (ValueError, SyntaxError):
        return default or []


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Core
    database_url: str = ""
    bot_secret_key: str = "changeme"
    admin_api_key: str = "changeme"
    log_level: str = "INFO"

    # 3x-UI panel
    api_url: str = ""
    admin_username: str = ""
    admin_password: str = ""

    # YooKassa
    yookassa_shop_id: str = ""
    yookassa_secret_key: str = ""
    disable_webhook_ip_check: bool = False

    # Telegram
    bot_token: str = ""
    bot_name: str = "VPNBot"
    support_chat_url: str = ""
    url_bot: str = ""

    # Bot behaviour
    admin_id_raw: str = "[0]"
    available_rates_raw: str = "[9, 8, 7]"
    available_connections_raw: str = "[11, 12]"
    default_pricing_plan: str = "10"
    trial_time: int = 30
    discounts: int = 3
    limit_ip: int = 3

    # Monitoring
    metrics_port: int = 9101
    webhook_path: str = "/api/v1/payments/webhook"


settings = Settings()

# ============================================================
# Module-level compat vars — used by copied bot modules via
# `from config import DATABASE_URL` pattern (flat imports)
# ============================================================
DATABASE_URL: str = settings.database_url
API_URL: str = settings.api_url
ADMIN_USERNAME: str = settings.admin_username
ADMIN_PASSWORD: str = settings.admin_password
YOOKASSA_SHOP_ID: str = settings.yookassa_shop_id
YOOKASSA_SECRET_KEY: str = settings.yookassa_secret_key
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

REFERRAL_BONUS_PERCENTAGES: dict = {
    "1": "0.10",
    "2": "0.05",
    "3": "0.02",
}

# Aliases expected by some bot modules
WEBHOOK_HOST: str = os.getenv("WEBHOOK_HOST", "0.0.0.0")
WEBHOOK_PORT: int = int(os.getenv("WEBHOOK_PORT", "8000"))
