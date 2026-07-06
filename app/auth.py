from dataclasses import dataclass
from typing import Optional

from fastapi import Header, HTTPException

from config import settings


async def verify_bot_secret(x_bot_secret: Optional[str] = Header(None)) -> None:
    if not x_bot_secret or x_bot_secret != settings.bot_secret_key:
        raise HTTPException(status_code=401, detail="Invalid bot secret")


async def verify_api_key(x_api_key: Optional[str] = Header(None)) -> None:
    if not x_api_key or x_api_key != settings.admin_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")


async def verify_admin_or_bot(
    x_api_key: Optional[str] = Header(None),
    x_bot_secret: Optional[str] = Header(None),
) -> None:
    """Allow access with either admin API key or bot service secret."""
    if x_api_key and x_api_key == settings.admin_api_key:
        return
    if x_bot_secret and x_bot_secret == settings.bot_secret_key:
        return
    raise HTTPException(status_code=401, detail="Invalid credentials")


@dataclass
class AdminPrincipal:
    admin_tg_id: Optional[int]


async def verify_admin_actor(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_admin_tg_id: Optional[str] = Header(None, alias="X-Admin-Tg-Id"),
    x_bot_secret: Optional[str] = Header(None, alias="X-Bot-Secret"),
) -> AdminPrincipal:
    """Деструктивные admin-операции: только X-API-Key. Bot-secret не принимается."""
    if not x_api_key or x_api_key != settings.admin_api_key:
        raise HTTPException(status_code=401, detail="Invalid admin API key")

    admin_tg_id: Optional[int] = None
    if x_admin_tg_id:
        try:
            admin_tg_id = int(x_admin_tg_id)
        except (ValueError, TypeError):
            admin_tg_id = None
    return AdminPrincipal(admin_tg_id=admin_tg_id)
