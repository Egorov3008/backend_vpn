"""
Core shared settings for VPN platform.

Used by both bot/ and backend/ as a single source of truth for
common configuration. Component-specific settings remain in their
own config.py files.
"""
from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# Referral bonus percentages: level → percentage
# Single source of truth used by both bot and backend
REFERRAL_BONUS_PERCENTAGES: dict = {
    "1": "0.10",  # 10% for level 1
    "2": "0.05",  # 5% for level 2
    "3": "0.02",  # 2% for level 3
}


def _find_env_file() -> Path | None:
    """
    Locate the .env file by walking up from this file's location.

    Priority:
    1. Project root .env (platform/.env) — preferred for monorepo
    2. bot/.env or backend/.env (component-specific) — fallback
    """
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / ".env"
        if candidate.exists():
            return candidate
    return None


class CoreSettings(BaseSettings):
    """
    Core shared settings for VPN platform.

    Loaded from .env in monorepo root. Components may extend this
    with their own component-specific fields.
    """

    model_config = SettingsConfigDict(
        env_file=str(_find_env_file()) if _find_env_file() else ".env",
        extra="ignore",
        populate_by_name=True,
    )

    # ── Database ──
    database_url: str = ""

    # ── Service-to-service auth ──
    bot_secret_key: str = Field(default="changeme", alias="BOT_SECRET_KEY")
    invite_token: str = Field(default="changeme", alias="INVITE_TOKEN")

    # ── YooKassa payment settings (shared) ──
    yookassa_shop_id: str = Field(default="", alias="YOOKASSA_SHOP_ID")
    yookassa_secret_key: str = Field(default="", alias="YOOKASSA_SECRET_KEY")
    disable_webhook_ip_check: bool = Field(
        default=False, alias="DISABLE_WEBHOOK_IP_CHECK"
    )
    receipt_customer_email: str = Field(
        default="", alias="RECEIPT_CUSTOMER_EMAIL"
    )

    # ── Bot behaviour (shared defaults) ──
    default_pricing_plan: str = Field(default="10", alias="DEFAULT_PRICING_PLAN")
    trial_time: int = Field(default=30, alias="TRIAL_TIME")
    discounts: int = Field(default=3, alias="DISCOUNTS")
    limit_ip: int = Field(default=3, alias="LIMIT_IP")

    # ── Admin config ──
    admin_tg_ids_raw: str = Field(default="[]", alias="ADMIN_TG_IDS")
    admin_api_key: str = Field(default="changeme", alias="ADMIN_API_KEY")

    # ── Monitoring ──
    metrics_port: int = Field(default=9101, alias="METRICS_PORT")
    webhook_path: str = Field(
        default="/api/v1/payments/webhook", alias="WEBHOOK_PATH"
    )

    def get_admin_tg_ids(self) -> List[int]:
        """Parse admin_tg_ids_raw as a JSON list of ints."""
        import ast
        try:
            result = ast.literal_eval(self.admin_tg_ids_raw)
            return [int(x) for x in result] if isinstance(result, list) else []
        except (ValueError, SyntaxError):
            return []


@lru_cache(maxsize=1)
def get_core_settings() -> CoreSettings:
    """Return cached CoreSettings instance."""
    return CoreSettings()


# Module-level singleton for convenient import:
# `from shared.config import core_settings`
core_settings = get_core_settings()
