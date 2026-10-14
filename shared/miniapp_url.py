"""Проверка и сборка ссылок на Telegram Mini App.

Копия есть в боте (``platform-bot/shared/miniapp_url.py``) — автосинхронизации
``shared/`` нет, при изменении правьте оба файла.
"""

from urllib.parse import urlencode, urlsplit, urlunsplit


def normalize_miniapp_url(value: str) -> str:
    """Возвращает нормализованный URL (пустой путь → ``/``) или "" если не задан.

    Telegram открывает WebApp только по https, без логина/пароля в URL и без
    фрагмента (в ``#`` Telegram сам дописывает ``tgWebAppData``). Ошибка в
    ссылке ломает все кнопки сразу, поэтому падаем на старте, а не молча.
    """
    value = (value or "").strip()
    if not value:
        return ""
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError(f"MINIAPP_URL должен быть https-ссылкой с хостом, получено: {value!r}")
    if parts.username or parts.password:
        raise ValueError("MINIAPP_URL не должен содержать логин или пароль")
    if parts.fragment:
        raise ValueError("MINIAPP_URL не должен содержать #фрагмент")
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))


def miniapp_link(base_url: str, **params: str) -> str:
    """Ссылка на экран Mini App: ``base?screen=pay&renew=<email>``."""
    if not params:
        return base_url
    sep = "&" if urlsplit(base_url).query else "?"
    return f"{base_url}{sep}{urlencode(params)}"
