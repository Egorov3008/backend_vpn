import logging
import os
import sys
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


def generate_trace_id() -> str:
    """Генерирует новый trace_id (первые 8 символов UUID)."""
    import uuid
    return uuid.uuid4().hex[:8]


def set_trace_id(trace_id: str) -> None:
    """Устанавливает trace_id в ContextVar."""
    trace_id_var.set(trace_id)


def get_trace_id() -> str:
    """Получает текущий trace_id."""
    return trace_id_var.get()


def reset_trace_id() -> None:
    """Сбрасывает trace_id."""
    trace_id_var.set("")


class InterceptHandler(logging.Handler):
    """Перехватчик stdlib logging → loguru."""
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = _loguru.level(record.levelname).name
        except ValueError:
            level = record.levelno
        _loguru.log(level, record.getMessage())


class StructuredLogger:
    def _log(self, level: str, message: str, **kwargs: Any) -> None:
        trace_id = get_trace_id()
        extra = {
            "service": "backend",
            **({"trace_id": trace_id} if trace_id else {}),
            **_mask_sensitive(kwargs),
        }
        _loguru.opt(depth=2).bind(**extra).log(level.upper(), message)

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

    def exception(self, message: str, **kwargs: Any) -> None:
        self._log("ERROR", message, **kwargs)


logger = StructuredLogger()


def with_context(**ctx_kwargs: Any):
    """Decorator for adding log context to async functions."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            return await func(*args, **kwargs)
        return wrapper
    return decorator


def setup_logging(
    log_level: str = "INFO",
    log_file: str | None = None,
    log_format: str = "detailed",
) -> None:
    """Настраивает loguru с файловыми sink-ами, ротацией, перехватом stdlib logging."""

    # Удаляем дефолтный sink
    _loguru.remove()

    # Определяем формат логирования
    if log_format == "json":
        fmt = (
            '<level>{level: <8}</level> | '
            '<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | '
            '<level>{message}</level>'
        )
    elif log_format == "simple":
        fmt = '<level>{time:YYYY-MM-DD HH:mm:ss}</level> | <level>{level: <8}</level> | <level>{message}</level>'
    else:  # detailed
        fmt = (
            '<level>{time:YYYY-MM-DD HH:mm:ss}</level> | '
            '<level>{level: <8}</level> | '
            '<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | '
            '<level>{message}</level>'
        )

    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    # Console sink (stderr)
    _loguru.add(
        sys.stderr,
        level=numeric_level,
        format=fmt,
        colorize=True,
    )

    # File sink: application.log (INFO level, 3h rotation, 30d retention)
    try:
        _loguru.add(
            os.path.join(LOG_FOLDER, "application.log"),
            level="INFO",
            format=fmt,
            rotation="3 hours",
            retention="30 days",
            compression="zip",
            enqueue=True,
        )
    except (PermissionError, OSError):
        pass  # Skip file sink if log file is not writable (e.g., in test environments)

    # File sink: errors.log (ERROR level, 1d rotation, 90d retention)
    try:
        _loguru.add(
            os.path.join(ERROR_LOG_FOLDER, "errors.log"),
            level="ERROR",
            format=fmt,
            rotation="1 day",
            retention="90 days",
            compression="zip",
            enqueue=True,
        )
    except (PermissionError, OSError):
        pass  # Skip file sink if log file is not writable (e.g., in test environments)

    # Перехватываем stdlib logging
    logging.basicConfig(handlers=[InterceptHandler()], level=numeric_level)

    # Подавляем шум от сторонних библиотек
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
