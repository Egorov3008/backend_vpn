"""
Tests for shared.config.core.CoreSettings.

Verifies that:
1. CoreSettings loads from .env correctly
2. Module-level singleton (core_settings) is accessible
3. REFERRAL_BONUS_PERCENTAGES is the single source of truth
4. Fallback values work when .env is missing fields
"""
import os
import pytest

from shared.config import (
    CoreSettings,
    core_settings,
    REFERRAL_BONUS_PERCENTAGES,
    get_core_settings,
)


class TestCoreSettingsImport:
    """Test that shared.config exports are accessible."""

    def test_core_settings_class_importable(self):
        """CoreSettings should be importable from shared.config."""
        assert CoreSettings is not None
        assert isinstance(CoreSettings, type)

    def test_core_settings_singleton_exists(self):
        """core_settings singleton should be importable."""
        assert core_settings is not None
        assert isinstance(core_settings, CoreSettings)

    def test_referral_bonus_percentages_defined(self):
        """REFERRAL_BONUS_PERCENTAGES should be a dict with 3 levels."""
        assert isinstance(REFERRAL_BONUS_PERCENTAGES, dict)
        assert "1" in REFERRAL_BONUS_PERCENTAGES
        assert "2" in REFERRAL_BONUS_PERCENTAGES
        assert "3" in REFERRAL_BONUS_PERCENTAGES

    def test_referral_bonus_values(self):
        """REFERRAL_BONUS_PERCENTAGES values should be percentage strings."""
        assert REFERRAL_BONUS_PERCENTAGES["1"] == "0.10"  # 10%
        assert REFERRAL_BONUS_PERCENTAGES["2"] == "0.05"  # 5%
        assert REFERRAL_BONUS_PERCENTAGES["3"] == "0.02"  # 2%

    def test_get_core_settings_returns_cached(self):
        """get_core_settings() should return the same instance on repeated calls."""
        s1 = get_core_settings()
        s2 = get_core_settings()
        assert s1 is s2
        assert s1 is core_settings


class TestCoreSettingsDefaults:
    """Test that CoreSettings loads values from .env correctly."""

    def test_trial_time_loaded_from_env(self):
        """trial_time is loaded from .env (TRIAL_TIME variable)."""
        # pydantic-settings auto-loads from .env on construction
        # If TRIAL_TIME is in .env, that value is used
        assert core_settings.trial_time in (7, 14, 30)  # Acceptable values

    def test_limit_ip_loaded(self):
        """limit_ip is loaded from .env or default."""
        assert core_settings.limit_ip >= 1

    def test_discounts_loaded(self):
        """discounts is loaded from .env or default (3)."""
        assert core_settings.discounts >= 0

    def test_metrics_port_loaded(self):
        """metrics_port is loaded from .env or default (9101)."""
        assert core_settings.metrics_port > 0

    def test_bot_secret_key_loaded_from_env(self):
        """bot_secret_key is loaded from .env BOT_SECRET_KEY."""
        # The .env file in the project root provides a real value
        assert core_settings.bot_secret_key != ""

    def test_yookassa_loaded(self):
        """YooKassa settings are loaded from .env or empty by default."""
        # yookassa_shop_id can be empty or have a real value depending on .env
        assert isinstance(core_settings.yookassa_shop_id, str)
        assert isinstance(core_settings.yookassa_secret_key, str)


class TestCoreSettingsEnvLoading:
    """Test that CoreSettings loads from environment variables."""

    def test_database_url_from_env(self, monkeypatch):
        """database_url should be loaded from DATABASE_URL env var."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://test@localhost/db")
        # Re-instantiate settings (cached singleton won't reflect env changes)
        settings = CoreSettings()
        assert settings.database_url == "postgresql://test@localhost/db"

    def test_bot_secret_key_from_env(self, monkeypatch):
        """bot_secret_key should be loaded from BOT_SECRET_KEY env var."""
        monkeypatch.setenv("BOT_SECRET_KEY", "super-secret-key")
        settings = CoreSettings()
        assert settings.bot_secret_key == "super-secret-key"

    def test_yookassa_from_env(self, monkeypatch):
        """YooKassa settings should be loaded from env vars."""
        monkeypatch.setenv("YOOKASSA_SHOP_ID", "shop-123")
        monkeypatch.setenv("YOOKASSA_SECRET_KEY", "secret-456")
        settings = CoreSettings()
        assert settings.yookassa_shop_id == "shop-123"
        assert settings.yookassa_secret_key == "secret-456"

    def test_trial_time_from_env(self, monkeypatch):
        """trial_time should be loaded from TRIAL_TIME env var."""
        monkeypatch.setenv("TRIAL_TIME", "14")
        settings = CoreSettings()
        assert settings.trial_time == 14


class TestAdminTgIds:
    """Test parsing of admin_tg_ids_raw."""

    def test_default_empty_list(self):
        """Default admin_tg_ids_raw is empty list."""
        settings = CoreSettings(database_url="")
        assert settings.get_admin_tg_ids() == []

    def test_parse_valid_list(self):
        """Valid JSON list should be parsed into list of ints."""
        settings = CoreSettings(database_url="", admin_tg_ids_raw="[123, 456, 789]")
        assert settings.get_admin_tg_ids() == [123, 456, 789]

    def test_parse_invalid_returns_empty(self):
        """Invalid JSON should return empty list (graceful fallback)."""
        settings = CoreSettings(database_url="", admin_tg_ids_raw="not-a-list")
        assert settings.get_admin_tg_ids() == []


class TestSharedConfigIntegration:
    """Test integration with backend and bot config."""

    def test_backend_can_import_shared(self):
        """Backend should be able to import from shared.config."""
        # This test would fail if backend/config.py couldn't import shared
        import sys
        sys.path.insert(0, "backend")
        try:
            from config import REFERRAL_BONUS_PERCENTAGES as backend_rbp
            assert backend_rbp is REFERRAL_BONUS_PERCENTAGES
        finally:
            sys.path.pop(0)

    def test_bot_can_import_shared(self):
        """Bot should be able to import from shared.config."""
        import sys
        sys.path.insert(0, "bot")
        try:
            from config import REFERRAL_BONUS_PERCENTAGES as bot_rbp
            assert bot_rbp is REFERRAL_BONUS_PERCENTAGES
        finally:
            sys.path.pop(0)
