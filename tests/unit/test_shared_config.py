"""Разбор ADMIN_ID (admin_tg_ids_raw) в shared.config.core.CoreSettings."""

from shared.config import CoreSettings


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
