import os
from contextvars import ContextVar
from functools import wraps
from typing import Any

from loguru import logger as _loguru

LOG_FOLDER = os.path.abspath("logs")
ERROR_LOG_FOLDER = os.path.abspath("logs_error")
os.makedirs(LOG_FOLDER, exist_ok=True)
os.makedirs(ERROR_LOG_FOLDER, exist_ok=True)

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")

SENSITIVE_KEYS = {
    "password", "secret", "api_key", "access_token", "BOT_TOKEN",
    "DATABASE_URL", "YOOKASSA_SECRET_KEY", "refresh_token", "shop_id",
    "secret_key", "ADMIN_PASSWORD",
}


def _mask_sensitive(data: Any) -> Any:
    if isinstance(data, dict):
        return {
            k: "***" if k in SENSITIVE_KEYS else _mask_sensitive(v)
            for k, v in data.items()
        }
    return data


class StructuredLogger:
    def _log(self, level: str, message: str, **kwargs: Any) -> None:
        _loguru.opt(depth=2).log(level.upper(), message, **_mask_sensitive(kwargs))

    def debug(self, message: str, **kwargs: Any) -> None:
        self._log("DEBUG", message, **kwargs)

    def info(self, message: str, **kwargs: Any) -> None:
        self._log("INFO", message, **kwargs)

    def warning(self, message: str, **kwargs: Any) -> None:
        self._log("WARNING", message, **kwargs)

    def error(self, message: str, **kwargs: Any) -> None:
        self._log("ERROR", message, **kwargs)

    def critical(self, message: str, **kwargs: Any) -> None:
        self._log("CRITICAL", message, **kwargs)

    def success(self, message: str, **kwargs: Any) -> None:
        self._log("SUCCESS", message, **kwargs)


logger = StructuredLogger()


def with_context(**ctx_kwargs: Any):
    """Decorator for adding log context to async functions (aiogram-agnostic)."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            return await func(*args, **kwargs)
        return wrapper
    return decorator
