"""Вход в веб-админку через Mini App отдельного админ-бота.

`POST /api/v1/admin/session` принимает `Telegram.WebApp.initData` админ-бота,
проверяет подпись токеном `ADMIN_BOT_TOKEN` и выдаёт Bearer-токен на
`ADMIN_SESSION_TTL_SECONDS` (8 ч) только тем, чей `tg_id` есть в `ADMIN_ID`.
С этим токеном работают обычные `/api/v1/admin/*` (`app/auth.py::SessionAdminAuth`),
`admin_tg_id` для аудита берётся из токена, а не из заголовка.

Почему Bearer, а не cookie: Mini App в Telegram Web открывается во фрейме
чужого сайта, где браузер не отправляет SameSite-cookie. Токен живёт только в
памяти страницы, поэтому CSRF ему не страшен; после перезагрузки страница
снова меняет initData на токен.
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import admin_ids
from app.core.miniapp_session import admin_sessions
from app.core.telegram import TelegramHashError, verify_webapp_init_data
from app.dependencies import get_pool
from app.rate_limit import rate_limit
from config import settings
from logger import logger
from services.admin_audit import AuditLogger

router = APIRouter(prefix="/admin", tags=["admin-auth"])


class AdminSessionRequest(BaseModel):
    init_data: str = Field(..., min_length=1, max_length=4096, description="Telegram.WebApp.initData of the admin bot")


class AdminSessionResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    tg_id: int


@router.post(
    "/session",
    response_model=AdminSessionResponse,
    dependencies=[Depends(rate_limit("admin-session", times=10, seconds=60))],
)
async def create_admin_session(body: AdminSessionRequest, pool=Depends(get_pool)) -> AdminSessionResponse:
    if not settings.admin_bot_token:
        # Вход закрыт, пока не задан токен админ-бота; бот работает по X-API-Key.
        raise HTTPException(status_code=503, detail="Admin login is not configured")
    try:
        fields = verify_webapp_init_data(
            body.init_data,
            settings.admin_bot_token,
            max_age_seconds=settings.admin_init_data_max_age_seconds,
        )
    except TelegramHashError as e:
        raise HTTPException(status_code=401, detail=str(e))

    tg_id = fields["user"]["id"]
    if tg_id not in admin_ids():
        logger.warning("Вход в админку: tg_id не в ADMIN_ID", tg_id=tg_id)
        raise HTTPException(status_code=403, detail="Not an admin")

    token, expires_in = admin_sessions.issue(tg_id)
    await AuditLogger(pool).record(tg_id, "admin_login", None)
    logger.info("Вход в админку", tg_id=tg_id)
    return AdminSessionResponse(access_token=token, expires_in=expires_in, tg_id=tg_id)
