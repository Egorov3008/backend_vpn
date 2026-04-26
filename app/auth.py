from typing import Optional

from fastapi import Header, HTTPException

from config import settings


async def verify_bot_secret(x_bot_secret: Optional[str] = Header(None)) -> None:
    if not x_bot_secret or x_bot_secret != settings.bot_secret_key:
        raise HTTPException(status_code=401, detail="Invalid bot secret")


async def verify_api_key(x_api_key: Optional[str] = Header(None)) -> None:
    if not x_api_key or x_api_key != settings.admin_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
