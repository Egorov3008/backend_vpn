"""Массовая рассылка из веб-админки (/api/v1/admin/broadcasts).

Сообщения уходят от пользовательского бота (BOT_TOKEN): у пользователей чат
именно с ним. Файл загружается в Telegram один раз через чат админа с этим
ботом, дальше всем уходит его file_id. Перед запуском админ получает
контрольную копию: если Telegram её не принял (битый HTML, чужой file_id),
рассылка не создаётся.

Все маршруты — только для админа (verify_admin_actor: сессия веб-админки или
X-API-Key + X-Admin-Tg-Id), ключа бота мало.
"""
import asyncpg
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile

from app.auth import AdminPrincipal, verify_admin_actor
from app.dependencies import get_pool
from app.factories import broadcast_jobs, build_broadcast_bot, build_broadcast_runner
from app.schemas.broadcasts import (
    AudienceResponse,
    AudiencesResponse,
    BroadcastCreateRequest,
    BroadcastMessageIn,
    BroadcastResponse,
    BroadcastsListResponse,
    MediaUploadResponse,
)
from config import settings
from services.admin_audit import AuditLogger
from services.broadcasts.audiences import AUDIENCES, find_audience
from services.broadcasts.media import MediaTooLarge, detect_media_type
from services.broadcasts.models import (
    BroadcastMessage,
    BroadcastStatus,
    Button,
    DeliveryStatus,
    Media,
    MediaType,
    SendResult,
)
from services.broadcasts.repository import BroadcastAlreadyRunning, BroadcastRepository
from services.broadcasts.telegram import TelegramBroadcastBot

router = APIRouter(prefix="/admin/broadcasts", tags=["admin"], dependencies=[Depends(verify_admin_actor)])


def _bot() -> TelegramBroadcastBot:
    bot = build_broadcast_bot()
    if not bot.configured:
        raise HTTPException(status_code=503, detail="BOT_TOKEN не задан: рассылать некому")
    return bot


def _admin_chat(principal: AdminPrincipal) -> int:
    if principal.admin_tg_id is None:
        raise HTTPException(status_code=400, detail="Неизвестен tg_id админа")
    return principal.admin_tg_id


def _raise_for_admin_send(result: SendResult) -> None:
    """Ответ Telegram на отправку в чат админа → понятная админу ошибка."""
    if result.ok:
        return
    if result.unauthorized:
        raise HTTPException(status_code=503, detail="Telegram не принял BOT_TOKEN")
    if result.status == DeliveryStatus.BLOCKED:
        raise HTTPException(
            status_code=409,
            detail="Пользовательский бот не может вам написать: откройте его в Telegram и нажмите «Старт»",
        )
    if result.network_error:
        raise HTTPException(status_code=502, detail="Telegram недоступен, попробуйте ещё раз")
    if result.retry_after is not None:
        raise HTTPException(status_code=503, detail=f"Telegram просит подождать {int(result.retry_after)} с")
    raise HTTPException(status_code=422, detail=f"Telegram не принял сообщение: {result.error}")


def _message(body: BroadcastMessageIn) -> BroadcastMessage:
    media = None
    if body.media is not None:
        media = Media(MediaType(body.media.type), body.media.file_id, body.media.file_name)
    button = None
    if body.button is not None:
        if body.button.kind == "miniapp":
            if not settings.miniapp_url:
                raise HTTPException(status_code=422, detail="MINIAPP_URL не задан: кнопку приложения не добавить")
            button = Button("miniapp", body.button.text, settings.miniapp_url)
        else:
            button = Button("url", body.button.text, body.button.url or "")
    return BroadcastMessage(
        text=body.text,
        media=media,
        button=button,
        disable_notification=body.disable_notification,
        pin=body.pin,
    )


@router.get("/audiences", response_model=AudiencesResponse)
async def admin_broadcast_audiences(
    pool: asyncpg.Pool = Depends(get_pool),
    principal: AdminPrincipal = Depends(verify_admin_actor),
):
    """Сегменты и сколько человек в каждом (без самого админа)."""
    repo = BroadcastRepository(pool)
    return {
        "audiences": [
            AudienceResponse(id=a.id, title=a.title, count=await repo.count_audience(a, principal.admin_tg_id))
            for a in AUDIENCES
        ]
    }


@router.post("/media", response_model=MediaUploadResponse)
async def admin_broadcast_upload_media(
    file: UploadFile = File(...),
    as_document: bool = Form(False),
    principal: AdminPrincipal = Depends(verify_admin_actor),
):
    """Загрузить фото, видео или файл в Telegram и получить file_id для рассылки.

    Файл уходит в чат админа с пользовательским ботом (без звука) и сразу
    удаляется оттуда: нужен только file_id этого бота."""
    bot = _bot()
    chat_id = _admin_chat(principal)
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    if size == 0:
        raise HTTPException(status_code=422, detail="Пустой файл")
    file_name = (file.filename or "file").rsplit("/", 1)[-1][:255]
    try:
        media_type = detect_media_type(file.content_type, file_name, size, as_document)
    except MediaTooLarge as e:
        raise HTTPException(status_code=413, detail=str(e)) from e

    result, media = await bot.upload(
        chat_id, media_type, file_name, file.content_type or "application/octet-stream", file.file
    )
    _raise_for_admin_send(result)
    if result.message_id is not None:
        await bot.delete_message(chat_id, result.message_id)
    return MediaUploadResponse(type=media.type.value, file_id=media.file_id, file_name=file_name, size=size)


@router.post("/preview", status_code=204)
async def admin_broadcast_preview(
    body: BroadcastMessageIn,
    principal: AdminPrincipal = Depends(verify_admin_actor),
):
    """Отправить черновик только себе — посмотреть, как его увидят пользователи."""
    result = await _bot().send(_admin_chat(principal), _message(body))
    _raise_for_admin_send(result)
    return Response(status_code=204)


@router.post("", response_model=BroadcastResponse, status_code=202)
async def admin_create_broadcast(
    body: BroadcastCreateRequest,
    pool: asyncpg.Pool = Depends(get_pool),
    principal: AdminPrincipal = Depends(verify_admin_actor),
):
    """Запустить рассылку. Сначала контрольная копия админу, затем фоновая отправка
    всем из сегмента (кроме админа). Одновременно идёт только одна рассылка (409)."""
    audience = find_audience(body.audience)
    if audience is None:
        raise HTTPException(status_code=422, detail="Неизвестный сегмент")
    message = _message(body)
    repo = BroadcastRepository(pool)
    running = await repo.active_ids()
    if running:
        raise HTTPException(status_code=409, detail=f"Уже идёт рассылка №{running[0]}")

    _raise_for_admin_send(await _bot().send(_admin_chat(principal), message))
    try:
        broadcast_id = await repo.create(principal.admin_tg_id, audience, message)
    except BroadcastAlreadyRunning as e:
        raise HTTPException(status_code=409, detail=f"Уже идёт рассылка №{e.broadcast_id}") from e
    await AuditLogger(pool).record(principal.admin_tg_id, "create_broadcast", str(broadcast_id))
    broadcast_jobs().start(broadcast_id, build_broadcast_runner(pool))
    return BroadcastResponse.build(await repo.get(broadcast_id))


@router.get("", response_model=BroadcastsListResponse)
async def admin_list_broadcasts(
    response: Response,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    pool: asyncpg.Pool = Depends(get_pool),
):
    items, total = await BroadcastRepository(pool).page(limit, offset)
    response.headers["X-Total-Count"] = str(total)
    return {"broadcasts": [BroadcastResponse.build(b) for b in items]}


@router.get("/{broadcast_id}", response_model=BroadcastResponse)
async def admin_get_broadcast(broadcast_id: int, pool: asyncpg.Pool = Depends(get_pool)):
    broadcast = await BroadcastRepository(pool).get(broadcast_id)
    if broadcast is None:
        raise HTTPException(status_code=404, detail="Рассылка не найдена")
    return BroadcastResponse.build(broadcast)


@router.post("/{broadcast_id}/cancel", response_model=BroadcastResponse)
async def admin_cancel_broadcast(
    broadcast_id: int,
    pool: asyncpg.Pool = Depends(get_pool),
    principal: AdminPrincipal = Depends(verify_admin_actor),
):
    """Остановить рассылку. Кому уже ушло — останется; остальным не отправится."""
    repo = BroadcastRepository(pool)
    if not await repo.request_cancel(broadcast_id):
        broadcast = await repo.get(broadcast_id)
        if broadcast is None:
            raise HTTPException(status_code=404, detail="Рассылка не найдена")
        return BroadcastResponse.build(broadcast)
    await AuditLogger(pool).record(principal.admin_tg_id, "cancel_broadcast", str(broadcast_id))
    if not broadcast_jobs().is_running(broadcast_id):
        # Задачи нет (процесс перезапущен, а продолжение не стартовало) — закрываем сами
        await repo.finish(broadcast_id, BroadcastStatus.CANCELED)
    return BroadcastResponse.build(await repo.get(broadcast_id))
