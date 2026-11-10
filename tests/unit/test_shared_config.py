"""Разбор ADMIN_ID (admin_tg_ids_raw) в shared.config.core.CoreSettings."""

import pytest

from shared.config import CoreSettings

# Не-placeholder секреты: CoreSettings отвергает дефолтные значения.
# ``_env_file=None`` — не читать .env репозитория: тест проверяет разбор поля,
# а не окружение разработчика (иначе локальный ADMIN_TG_IDS подменяет значения).
_ISOLATED_SETTINGS = {
    "database_url": "",
    "_env_file": None,
    "BOT_SECRET_KEY": "test-bot-secret-not-for-production-01",
    "INVITE_TOKEN": "test-invite-token-not-for-production-01",
    "ADMIN_API_KEY": "test-admin-api-key-not-for-production-1",
}


@pytest.fixture(autouse=True)
def _clear_ambient_admin_env(monkeypatch):
    """Переменные из shell тоже не должны влиять на разбор."""
    monkeypatch.delenv("ADMIN_TG_IDS", raising=False)


def _settings(**overrides) -> CoreSettings:
    return CoreSettings(**_ISOLATED_SETTINGS, **overrides)


class TestAdminTgIds:
    """Test parsing of admin_tg_ids_raw."""

    def test_default_empty_list(self):
        """Default admin_tg_ids_raw is empty list."""
        settings = _settings()
        assert settings.get_admin_tg_ids() == []

    def test_parse_valid_list(self):
        """Valid JSON list should be parsed into list of ints."""
        settings = _settings(admin_tg_ids_raw="[123, 456, 789]")
        assert settings.get_admin_tg_ids() == [123, 456, 789]

    def test_parse_invalid_returns_empty(self):
        """Invalid JSON should return empty list (graceful fallback)."""
        settings = _settings(admin_tg_ids_raw="not-a-list")
        assert settings.get_admin_tg_ids() == []
