"""
Лендинг для генерации анонимных 24-часовых VPN-ключей для Telegram-доступа.

Воронка:
  Анонимный посетитель → POST /landing/quick-key → 24ч-ключ с limit_ip=1
    → пользователь импортирует ключ в Happ / v2rayNG / Streisand
    → по истечении <6ч CTA "Продлить в Telegram-боте"
    → /start landing_<uid> в боте → 4 сценария (см. bot/handlers/start_from_landing.py)

Безопасность:
  - email НЕ собирается (0 PII)
  - ключ работает 24ч и автоматически умирает
  - limit_ip=1 (1 устройство)
  - подписанная HMAC-кука tg_landing_id на 90 дней (только для state)
  - inbound изолирован (XUI_INBOUND_ID_LANDING) — см. также .env AVAILABLE_CONNECTIONS
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timedelta
from typing import Optional

import asyncpg
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel

from app.auth import verify_bot_secret
from app.dependencies import get_cache, get_pool, get_service_data
from app.factories import build_key_services
from config import settings
from database.service import DataService
from logger import logger
from models import Tariff
from services.cache.key_manager import CacheKeyManager
from services.cache.service import CacheService
from services.core.data.service import ServiceDataModel
from services.core.user.utils.saver import SeverUser

router = APIRouter(
    prefix="/landing",
    tags=["landing"],
    dependencies=[Depends(verify_bot_secret)],
)


# Константы
LANDING_KEY_DURATION_HOURS = 24
LANDING_KEY_LIMIT_IP = 1
LANDING_COOKIE_MAX_AGE = 90 * 24 * 3600  # 90 дней
EXPIRING_THRESHOLD_HOURS = 6
LANDING_BOT_LINK_PREFIX = "https://t.me/"  # дополняется settings.bot_name


# Схемы ответов
class QuickKeyResponse(BaseModel):
    """Ответ на POST /landing/quick-key"""
    key_value: str
    expires_at_ms: int
    remaining_seconds: int
    deep_link_happ: str
    deep_link_bot: str
    state: str  # "active"


class LandingStateResponse(BaseModel):
    """Ответ на GET /landing/state"""
    state: str  # "new" | "active" | "expiring" | "expired" | "converted"
    key_value: Optional[str] = None
    expires_at_ms: Optional[int] = None
    remaining_seconds: Optional[int] = None
    deep_link_happ: Optional[str] = None
    deep_link_bot: Optional[str] = None
    bot_url: Optional[str] = None


# =============================================================================
# Cookie-утилиты (HMAC-подпись)
# =============================================================================
def _cookie_secret() -> str:
    """HMAC-секрет. Если не задан — fallback на bot_secret_key."""
    return settings.landing_cookie_secret or settings.bot_secret_key


def _sign_cookie(landing_uid: str) -> str:
    """Подписать landing_uid → cookie_value (base64.signature)."""
    payload = {
        "uid": landing_uid,
        "exp": int(time.time()) + LANDING_COOKIE_MAX_AGE,
    }
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).rstrip(b"=").decode()
    sig = hmac.new(
        _cookie_secret().encode(), payload_b64.encode(), hashlib.sha256
    ).hexdigest()[:16]
    return f"{payload_b64}.{sig}"


def _verify_cookie(cookie_value: str) -> Optional[str]:
    """Верифицировать cookie → landing_uid или None."""
    if not cookie_value or "." not in cookie_value:
        return None
    payload_b64, sig = cookie_value.rsplit(".", 1)
    expected_sig = hmac.new(
        _cookie_secret().encode(), payload_b64.encode(), hashlib.sha256
    ).hexdigest()[:16]
    if not hmac.compare_digest(expected_sig, sig):
        return None
    try:
        # Восстанавливаем padding base64
        padding = "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
    except (ValueError, json.JSONDecodeError):
        return None
    if payload.get("exp", 0) < time.time():
        return None  # кука истекла
    return payload.get("uid")


# =============================================================================
# Вспомогательные функции
# =============================================================================
def _pseudo_tg_id(landing_uid: str) -> int:
    """Генерирует отрицательный tg_id для анонимного юзера.

    Гарантии:
      - Всегда отрицательный (не пересечётся с реальными Telegram ID)
      - Детерминированный для одного landing_uid
      - Укладывается в bigint
    """
    digest = hashlib.sha256(landing_uid.encode()).digest()[:8]
    n = int.from_bytes(digest, "big") % (10**9)
    return -n


def _build_deep_links(key_value: str, landing_uid: str) -> tuple[str, str]:
    """Формирует deep-link в Happ (открыть и импортировать) и в Telegram-бота."""
    import urllib.parse

    # Happ: deep-link "import" для импорта ключа из буфера обмена / URL
    # Формат: happ://import/<url-encoded-config>
    deep_link_happ = f"happ://import/{urllib.parse.quote(key_value, safe='')}"

    # Telegram-бот: /start landing_<uid> — бот подхватит и привяжет/выдаст trial
    bot_name = settings.bot_name or "TolkoDlyaSv0ih_Bot"
    deep_link_bot = f"{LANDING_BOT_LINK_PREFIX}{bot_name}?start=landing_{landing_uid}"

    return deep_link_happ, deep_link_bot


def _build_landing_tariff() -> Tariff:
    """Виртуальный in-memory тариф для лендинг-ключа (НЕ из БД)."""
    return Tariff(
        id=999,  # не пересекается с реальными тарифами в БД
        name_tariff="landing_24h",
        amount=0.0,
        description="Анонимный 24-часовой ключ для доступа в Telegram",
        limit_ip=LANDING_KEY_LIMIT_IP,
        period=1,  # 1 день (используется только как fallback; реальная длительность — 24ч)
        traffic_limit=0,
    )


async def _get_key_by_landing_uid(
    service_data: ServiceDataModel,
    pool: asyncpg.Pool,
    landing_uid: str,
):
    """Достать ключ из БД по landing_uid (fallback кеш→БД)."""
    # Сначала ищем в кеше (быстро)
    all_keys = service_data.cache_service.keys.all() if hasattr(
        service_data.cache_service.keys, "all"
    ) else []
    for k in all_keys:
        if getattr(k, "landing_uid", None) == landing_uid:
            return k

    # Fallback: прямой SQL через data_service
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM keys WHERE landing_uid = $1 LIMIT 1", landing_uid
            )
        if row is None:
            return None
        # Преобразуем Row → Key через фильтр известных полей (защита BaseRepository)
        from models.keys.key import Key
        return Key(**dict(row))
    except Exception as e:
        logger.error(
            "Ошибка при поиске ключа по landing_uid",
            landing_uid=landing_uid,
            error=str(e),
        )
        return None


# =============================================================================
# POST /landing/quick-key
# =============================================================================
@router.post("/quick-key", response_model=QuickKeyResponse)
async def create_quick_key(
    response: Response,
    pool: asyncpg.Pool = Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
    cache: CacheService = Depends(get_cache),
) -> QuickKeyResponse:
    """Создать анонимный 24-часовой ключ для лендинга.

    - Без email, без регистрации
    - inbound_id берётся из settings.xui_inbound_id_landing
    - limit_ip=1 (1 устройство)
    - expiry = now + 24ч
    - tg_id = отрицательный хэш landing_uid
    - Устанавливает подписанную HMAC-куку tg_landing_id на 90 дней
    """
    if not settings.xui_inbound_id_landing:
        raise HTTPException(
            status_code=500,
            detail="XUI_INBOUND_ID_LANDING не настроен на сервере",
        )

    # 1. Генерируем уникальный landing_uid
    landing_uid = uuid.uuid4().hex[:16]

    # 2. Формируем детерминированный псевдо-tg_id
    pseudo_tg_id = _pseudo_tg_id(landing_uid)

    # 3. Регистрируем анонимного юзера (если ещё нет — крайне маловероятно
    #    из-за детерминизма, но возможно если процесс упал в прошлый раз)
    try:
        existing_user = await service_data.users.get_data(pseudo_tg_id)
        if not existing_user:
            saver = SeverUser(service_data)
            await saver.register_user(
                pool,
                tg_id=pseudo_tg_id,
                username=f"landing_{landing_uid[:8]}",
                first_name="Anonymous",
                last_name=None,
                language_code="ru",
                server_id=settings.xui_server_id,
            )
            # Получаем только что созданного юзера для кеша
            new_user = await service_data.users.get_data(pseudo_tg_id)
            if new_user:
                await cache.users.set(
                    CacheKeyManager.user(pseudo_tg_id), new_user
                )
    except Exception as e:
        logger.error(
            "Ошибка при регистрации анонимного юзера",
            landing_uid=landing_uid,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail="Failed to create anonymous user")

    # 4. Создаём ключ через стандартный пайплайн, но с принудительным inbound_id
    data_service = DataService()
    create_key_svc, _, _ = build_key_services(pool, service_data, cache, data_service)

    tariff = _build_landing_tariff()
    # Рассчитываем number_of_months так, чтобы получить ~24ч
    # (tariff.period=1 день, значит number_of_months=1 = 1 день = 24ч)
    result = await create_key_svc.proces(
        tg_id=pseudo_tg_id,
        tariff=tariff,
        server_id=settings.xui_server_id,
        conn=pool,
        number_of_months=1,
        inbound_id_override=settings.xui_inbound_id_landing,
    )

    if not result:
        raise HTTPException(status_code=500, detail="Failed to create landing key")

    # 5. Обновляем запись ключа: ставим landing_uid, пересчитываем expiry_time
    #    (FormationKey даёт expiry на основе tariff.period*months; хотим ровно 24ч от now)
    new_expiry_ms = int(
        (datetime.now() + timedelta(hours=LANDING_KEY_DURATION_HOURS)).timestamp() * 1000
    )
    key_obj = await service_data.keys.get_data(result["email"])
    if not key_obj:
        raise HTTPException(status_code=500, detail="Created key not found")

    key_obj.landing_uid = landing_uid
    key_obj.expiry_time = new_expiry_ms
    key_obj.limit_ip = LANDING_KEY_LIMIT_IP
    await service_data.keys.update(pool, key_obj, search_data={"email": key_obj.email})

    # Обновляем в кеше тоже
    await cache.keys.set(CacheKeyManager.key(key_obj.email), key_obj)

    # 6. Формируем ответ и подписываем куку
    remaining = max(0, (new_expiry_ms - int(time.time() * 1000)) // 1000)
    deep_link_happ, deep_link_bot = _build_deep_links(key_obj.key, landing_uid)

    response.set_cookie(
        key="tg_landing_id",
        value=_sign_cookie(landing_uid),
        max_age=LANDING_COOKIE_MAX_AGE,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )

    logger.info(
        "Landing quick-key создан",
        landing_uid=landing_uid,
        pseudo_tg_id=pseudo_tg_id,
        inbound_id=settings.xui_inbound_id_landing,
        email=key_obj.email,
    )

    return QuickKeyResponse(
        key_value=key_obj.key,
        expires_at_ms=new_expiry_ms,
        remaining_seconds=remaining,
        deep_link_happ=deep_link_happ,
        deep_link_bot=deep_link_bot,
        state="active",
    )


# =============================================================================
# GET /landing/state
# =============================================================================
@router.get("/state", response_model=LandingStateResponse)
async def get_state(
    tg_landing_id: Optional[str] = Cookie(None),
    pool: asyncpg.Pool = Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
) -> LandingStateResponse:
    """Вернуть состояние лендинг-ключа по подписанной куке.

    Возможные состояния:
      - new       — нет куки или невалидная кука
      - active    — ключ живёт, до истечения > 6ч
      - expiring  — ключ живёт, до истечения < 6ч (усиленный CTA)
      - expired   — ключ истёк
      - converted — ключ привязан к tg_id (юзер дошёл до бота)
    """
    if not tg_landing_id:
        return LandingStateResponse(state="new")

    landing_uid = _verify_cookie(tg_landing_id)
    if not landing_uid:
        return LandingStateResponse(state="new")

    key_obj = await _get_key_by_landing_uid(service_data, pool, landing_uid)
    if not key_obj:
        return LandingStateResponse(state="expired")

    now_ms = int(time.time() * 1000)
    expiry_ms = int(key_obj.expiry_time or 0)

    # Определяем состояние
    if expiry_ms <= now_ms:
        return LandingStateResponse(state="expired")

    remaining_seconds = (expiry_ms - now_ms) // 1000

    deep_link_happ, deep_link_bot = _build_deep_links(key_obj.key, landing_uid)

    state = "expiring" if remaining_seconds < EXPIRING_THRESHOLD_HOURS * 3600 else "active"

    return LandingStateResponse(
        state=state,
        key_value=key_obj.key,
        expires_at_ms=expiry_ms,
        remaining_seconds=remaining_seconds,
        deep_link_happ=deep_link_happ,
        deep_link_bot=deep_link_bot,
    )


# =============================================================================
# POST /landing/mark-converted (admin endpoint — вызывается ботом)
# =============================================================================
class MarkConvertedRequest(BaseModel):
    tg_id: int


@router.post("/mark-converted/{landing_uid}")
async def mark_converted(
    landing_uid: str,
    body: MarkConvertedRequest,
    pool: asyncpg.Pool = Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
    cache: CacheService = Depends(get_cache),
):
    """Проставить converted_tg_id для ключа, найденного по landing_uid.

    Вызывается ботом, когда юзер приходит по /start landing_<uid>.
    Временный ключ НЕ отключается — продолжает жить до 24ч.
    """
    key_obj = await _get_key_by_landing_uid(service_data, pool, landing_uid)
    if not key_obj:
        raise HTTPException(status_code=404, detail="Landing key not found")

    # Не перезаписываем, если уже был привязан (идемпотентность)
    if key_obj.converted_tg_id is not None and key_obj.converted_tg_id != body.tg_id:
        logger.warning(
            "Landing key уже привязан к другому tg_id",
            landing_uid=landing_uid,
            current=key_obj.converted_tg_id,
            new=body.tg_id,
        )

    key_obj.converted_tg_id = body.tg_id
    # НЕ удаляем из x-ui — ключ продолжает работать до 24ч
    await service_data.keys.update(pool, key_obj, search_data={"email": key_obj.email})
    await cache.keys.set(CacheKeyManager.key(key_obj.email), key_obj)

    logger.info(
        "Landing key помечен как converted",
        landing_uid=landing_uid,
        tg_id=body.tg_id,
        email=key_obj.email,
    )

    return {"ok": True, "email": key_obj.email}
