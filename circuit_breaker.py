"""Lightweight async circuit breaker for 3x-UI API calls.

Replaces the broken ``pybreaker>=1.0.0`` package, whose ``call_async``
references the removed ``tornado.gen`` module and raises ``NameError``
on every invocation under modern Tornado.

Public API mirrors the subset of ``pybreaker.CircuitBreaker`` used in
``client.py`` and the existing test suite:

* ``CircuitBreaker(fail_max=5, reset_timeout=30, success_threshold=1, name=...)``
* ``async circuit.call_async(fn, *args, **kwargs)``
* ``circuit.current_state`` → ``"closed" | "open" | "half_open"``
* ``circuit.half_open()`` / ``circuit.close()`` / ``circuit.open()``
* ``CircuitBreakerError`` raised when the breaker is open

State machine (single asyncio event loop — no locks required):

* **closed** — every call goes through. ``consecutive_failures`` is
  incremented on exception and reset on success. When it reaches
  ``fail_max`` the breaker transitions to **open** and stamps
  ``opened_at``.
* **open** — calls raise ``CircuitBreakerError`` immediately, no I/O
  is performed. Once ``reset_timeout`` seconds elapse since
  ``opened_at`` the next call auto-transitions to **half_open**.
* **half_open** — at most one call is allowed. On success the breaker
  returns to **closed**; on failure it goes back to **open** with a
  fresh ``opened_at``.
"""
from __future__ import annotations

import time
from typing import Any, Awaitable, Callable, Optional


class CircuitBreakerError(Exception):
    """Raised when a call is short-circuited by an open breaker."""


class CircuitBreaker:
    """Minimal async circuit breaker.

    Parameters
    ----------
    fail_max:
        Consecutive failures required to open the breaker.
    reset_timeout:
        Seconds to wait in ``open`` before the next call may try again
        (the breaker auto-transitions to ``half_open`` on that call).
    success_threshold:
        Successive successes in ``half_open`` required to close the
        breaker. Defaults to ``1`` — a single good call closes it,
        matching the previous ``pybreaker`` behaviour used in
        ``client.py``.
    name:
        Human-readable label, used only in ``__repr__``/``str``.
    """

    def __init__(
        self,
        fail_max: int = 5,
        reset_timeout: float = 30,
        success_threshold: int = 1,
        name: str = "circuit_breaker",
    ) -> None:
        if fail_max < 1:
            raise ValueError("fail_max must be >= 1")
        if reset_timeout < 0:
            raise ValueError("reset_timeout must be >= 0")
        if success_threshold < 1:
            raise ValueError("success_threshold must be >= 1")

        self.fail_max = fail_max
        self.reset_timeout = float(reset_timeout)
        self.success_threshold = success_threshold
        self.name = name

        self._state = "closed"
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._opened_at: Optional[float] = None

    # ------------------------------------------------------------------
    # Public state accessors
    # ------------------------------------------------------------------
    @property
    def current_state(self) -> str:
        """Return ``"closed"``, ``"open"`` or ``"half_open"``.

        When the breaker is ``open`` and the configured ``reset_timeout``
        has elapsed since ``opened_at``, the state is *lazily*
        promoted to ``half_open`` so the next ``call_async`` is the
        probe that decides closed-vs-open.
        """
        self._maybe_recover_from_open()
        return self._state

    def half_open(self) -> None:
        """Force the breaker into ``half_open`` (used by tests)."""
        self._state = "half_open"
        self._consecutive_successes = 0

    def close(self) -> None:
        """Reset the breaker to ``closed``."""
        self._state = "closed"
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._opened_at = None

    def open(self) -> None:
        """Force the breaker into ``open`` (used by tests)."""
        self._state = "open"
        self._opened_at = time.monotonic()
        self._consecutive_successes = 0

    # ------------------------------------------------------------------
    # Core call wrapper
    # ------------------------------------------------------------------
    async def call_async(
        self,
        fn: Callable[..., Awaitable[Any]],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Execute ``await fn(*args, **kwargs)`` under breaker control."""
        self._maybe_recover_from_open()

        if self._state == "open":
            raise CircuitBreakerError(
                f"CircuitBreaker '{self.name}' is open"
            )

        try:
            result = await fn(*args, **kwargs)
        except BaseException:
            self._on_failure()
            raise

        self._on_success()
        return result

    # ------------------------------------------------------------------
    # Internal state transitions
    # ------------------------------------------------------------------
    def _maybe_recover_from_open(self) -> None:
        if self._state != "open" or self._opened_at is None:
            return
        if (time.monotonic() - self._opened_at) >= self.reset_timeout:
            self._state = "half_open"
            self._consecutive_successes = 0
            self._opened_at = None

    def _on_success(self) -> None:
        self._consecutive_failures = 0
        if self._state == "half_open":
            self._consecutive_successes += 1
            if self._consecutive_successes >= self.success_threshold:
                self._state = "closed"
                self._consecutive_successes = 0
        # In "closed" state nothing else is needed.

    def _on_failure(self) -> None:
        # A failure while half-open immediately reopens the breaker.
        if self._state == "half_open":
            self._state = "open"
            self._opened_at = time.monotonic()
            self._consecutive_successes = 0
            return

        # In "closed" state accumulate failures until threshold.
        self._consecutive_failures += 1
        self._consecutive_successes = 0
        if self._consecutive_failures >= self.fail_max:
            self._state = "open"
            self._opened_at = time.monotonic()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"CircuitBreaker(name={self.name!r}, state={self._state!r}, "
            f"failures={self._consecutive_failures}/{self.fail_max})"
        )
