"""Derived subscription status from Key.expiry_time / Key.grace_expiry."""
import time


class KeyStatus:
    ACTIVE = "active"
    GRACE = "grace"
    EXPIRED = "expired"
    NONE = "none"

    @staticmethod
    def of(key, now_ms: int | None = None) -> str:
        grace_expiry = getattr(key, "grace_expiry", None)
        if grace_expiry is None:
            return KeyStatus.NONE
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        expiry = int(getattr(key, "expiry_time", 0) or 0)
        if now < expiry:
            return KeyStatus.ACTIVE
        if now < int(grace_expiry):
            return KeyStatus.GRACE
        return KeyStatus.EXPIRED