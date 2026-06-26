"""Derived subscription status from Key.expiry_time / Key.grace_expiry.

Status is NOT stored — it is derived on read. Statuses:
  ACTIVE  — within the paid period (now < expiry_time)
  GRACE   — paid period over, grace window open (expiry_time <= now < grace_expiry)
  EXPIRED — grace window elapsed (now >= grace_expiry, or no grace and now >= expiry_time)
  NONE    — no key, or no expiry_time

``grace_expiry`` is read defensively: a key without it (legacy, or pre-Task-3)
is treated as having no grace window, so it classifies by ``expiry_time`` alone
(ACTIVE or EXPIRED) rather than collapsing to NONE.
"""
import time


class KeyStatus:
    ACTIVE = "active"
    GRACE = "grace"
    EXPIRED = "expired"
    NONE = "none"

    @staticmethod
    def of(key, now_ms: int | None = None) -> str:
        if key is None:
            return KeyStatus.NONE
        expiry = getattr(key, "expiry_time", None)
        if not expiry:
            return KeyStatus.NONE
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        if now < int(expiry):
            return KeyStatus.ACTIVE
        grace = int(getattr(key, "grace_expiry", 0) or 0)
        if now < grace:
            return KeyStatus.GRACE
        return KeyStatus.EXPIRED