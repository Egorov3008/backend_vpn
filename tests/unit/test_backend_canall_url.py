from config import settings


def test_canall_url_field_exists():
    """backend Settings читает CANALL_URL из env (алиас)."""
    # Поле должно быть строкой (возможно пустой, если env не задан в тестах).
    assert hasattr(settings, "canall_url")
    assert isinstance(settings.canall_url, str)


def test_canall_url_alias_resolves():
    """При CANALL_URL в env Settings его подхватывает."""
    from config import Settings

    s = Settings(CANALL_URL="https://t.me/my_channel")
    assert s.canall_url == "https://t.me/my_channel"