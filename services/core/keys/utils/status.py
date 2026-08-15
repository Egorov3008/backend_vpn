"""Derived subscription status from Key.expiry_time.

Status is NOT stored — it is derived on read. Statuses:
  ACTIVE  — within the paid period (now < expiry_time)
  EXPIRED — paid period over (now >= expiry_time)
  NONE    — no key, or no expiry_time

Access control itself is not driven by this status — the 3x-ui panel cuts
VPN access on its own once its client's ``expiryTime`` passes. ``KeyStatus``
is only used for renewal eligibility and notification/bonus logic.
"""
import time


class KeyStatus:
    ACTIVE = "active"
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
        return KeyStatus.EXPIRED