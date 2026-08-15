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
  - inbound изолирован (XUI_INBOUND_ID_LANDING), не входит в AVAILABLE_CONNECTIONS
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
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
from client import XUISession
from config import DEFAULT_PRICING_PLAN, settings
from database.service import DataService
from logger import logger
from models import Tariff
from models.referrals.referral_redemption import ReferralRedemption
from services.cache.key_manager import CacheKeyManager
from services.cache.loader import LoadingService
from services.cache.service import CacheService
from services.core.data.service import ServiceDataModel
from services.core.keys.utils.calculator import ExpiryCalculator
from services.core.keys.utils.landing_upgrade import upgrade_landing_key
from services.core.user.utils.saver import SeverUser
from services.core.user.utils.trial import TrialService

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

# Referral cookie (tg_ref) — фиксирует «от кого пришёл» пользователь при заходе
# на лендинг с ?ref=… Зеркалит tg_landing_id (тот же секрет, тот же формат).
TG_REF_COOKIE_NAME = "tg_ref"
TG_REF_COOKIE_MAX_AGE = 90 * 24 * 3600  # 90 дней
_REF_TOKEN_REGEX = re.compile(r"^ref_[0-9a-f]{12}$")

# In-memory TTL-кеш vless-конфигов по subscription URL.
# Ключи landing-24h живут недолго, поэтому кеш не растёт бесконтрольно.
_VLESS_CACHE: dict[str, tuple[str, float]] = {}
_VLESS_CACHE_TTL_SECONDS = 300


# Схемы ответов
class QuickKeyResponse(BaseModel):
    """Ответ на POST /landing/quick-key"""
    key_value: str
    expires_at_ms: int
    remaining_seconds: int
    deep_link_happ: str
    deep_link_bot: str
    state: str  # "active"
    landing_uid: Optional[str] = None  # для JS-склейки deeplink (landing_<uid>_ref_<token>)


class LandingStateResponse(BaseModel):
    """Ответ на GET /landing/state"""
    state: str  # "new" | "active" | "expiring" | "expired" | "converted"
    key_value: Optional[str] = None
    expires_at_ms: Optional[int] = None
    remaining_seconds: Optional[int] = None
    deep_link_happ: Optional[str] = None
    deep_link_bot: Optional[str] = None
    bot_url: Optional[str] = None
    already_registered: bool = False
    landing_uid: Optional[str] = None  # для JS-склейки deeplink


class SetRefCookieRequest(BaseModel):
    """Тело POST /landing/set-ref-cookie."""
    ref_token: str


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
# Referral cookie (tg_ref) — фиксирует «от кого пришёл» при ?ref=… на лендинге
# =============================================================================
def _sign_ref_cookie(ref_token: str) -> str:
    """Подписать ref_token → cookie_value (base64.signature). Зеркалит _sign_cookie.

    Payload: ``{"ref": <token>, "exp": <unix_ts>}``. Использует тот же секрет
    (LANDING_COOKIE_SECRET / fallback bot_secret_key), что и tg_landing_id.
    """
    payload = {
        "ref": ref_token,
        "exp": int(time.time()) + TG_REF_COOKIE_MAX_AGE,
    }
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).rstrip(b"=").decode()
    sig = hmac.new(
        _cookie_secret().encode(), payload_b64.encode(), hashlib.sha256
    ).hexdigest()[:16]
    return f"{payload_b64}.{sig}"


def _verify_ref_cookie(cookie_value) -> Optional[str]:
    """Верифицировать tg_ref cookie → ref_token или None.

    Принимает строку или объект, приводимый к строке (например, Starlette Cookie).
    Некорректные/пустые/просроченные куки молча возвращают None.
    """
    if not isinstance(cookie_value, str):
        if cookie_value is None:
            return None
        try:
            cookie_value = str(cookie_value)
        except Exception:
            return None
    if not cookie_value or "." not in cookie_value:
        return None
    payload_b64, sig = cookie_value.rsplit(".", 1)
    expected_sig = hmac.new(
        _cookie_secret().encode(), payload_b64.encode(), hashlib.sha256
    ).hexdigest()[:16]
    if not hmac.compare_digest(expected_sig, sig):
        return None
    try:
        padding = "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
    except (ValueError, json.JSONDecodeError):
        return None
    if payload.get("exp", 0) < time.time():
        return None
    return payload.get("ref")


def _is_valid_ref_format(token: str) -> bool:
    """Валидация формата ``ref_<12hex>`` перед походом в БД."""
    return bool(token) and bool(_REF_TOKEN_REGEX.match(token))


def _normalize_ref_token(raw: Optional[str]) -> Optional[str]:
    """Привести ref-token к каноническому ``ref_<12hex>``.

    Принимает как полный токен ``ref_abc123def456`` (из куки/backend), так и
    чистую 12-hex часть ``abc123def456`` (из deeplink, который бот распарсил).
    Возвращает None, если формат не валиден.
    """
    if not raw:
        return None
    token = raw.strip().lower()
    if _is_valid_ref_format(token):
        return token
    # Чистая 12-hex часть без префикса ref_
    if re.match(r"^[0-9a-f]{12}$", token):
        return f"ref_{token}"
    return None


async def _attach_referral_if_any(
    pool: asyncpg.Pool,
    service_data: ServiceDataModel,
    cache: CacheService,
    tg_ref: Optional[str],
    referred_tg_id: int,
    raw_ref_token: Optional[str] = None,
) -> bool:
    """Слить реферера в ``users.referral_id`` и создать ``ReferralRedemption``.

    Вызывается из ``claim_landing_key`` (новый юзер) и ``mark_converted``
    (существующий юзер) после успешной upgrade/mark. Источник ref-token:

      - HMAC-кука ``tg_ref`` (ставится лендингом при заходе с ``?ref=…``);
      - поле ``ref_token`` в теле запроса (пробрасывается ботом из deeplink).

    Кука имеет приоритет; если её нет — используем ``raw_ref_token``.
    Защиты:

      - self-referral (``referrer_tg_id == referred_tg_id``) — отсекается;
      - уже выставленный ``users.referral_id`` — НЕ перезаписывается (антифрод);
      - дубликат ``referral_redemptions`` (UNIQUE по referred_tg_id) — игнорируется;
      - невалидная/просроченная/чужая кука или токен — молча возвращаемся.

    Возвращает True, если merge выполнен (для логов).
    """
    ref_token: Optional[str] = None
    if tg_ref:
        ref_token = _verify_ref_cookie(tg_ref)
    if not ref_token and raw_ref_token:
        ref_token = _normalize_ref_token(raw_ref_token)
    if not ref_token or not _is_valid_ref_format(ref_token):
        return False

    link = await service_data.referral_links.get_by(token=ref_token)
    if not link:
        return False

    referrer_tg_id = link.referrer_tg_id
    if referrer_tg_id == referred_tg_id:
        logger.warning(
            "Self-referral attempt via landing cookie, skipping",
            referred=referred_tg_id,
        )
        return False

    # Атомарно записываем referral_id только если он ещё NULL.
    # Раньше был read-check-write (get_data → if referral_id is None → update) —
    # это TOCTOU: два concurrent claim для одного referred_tg_id с разными
    # реферерами оба видели referral_id=None, оба писали (clobber), и
    # referral_id расходился с referral_redemptions (UNIQUE ловил только дубль
    # redemption). Теперь выигрывает ровно один запрос — тот же, что вставит
    # redemption, так что referral_id и redemption всегда согласованы.
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE users "
            "SET referral_id = $1 "
            "WHERE tg_id = $2 AND referral_id IS NULL "
            "RETURNING referral_id",
            referrer_tg_id, referred_tg_id,
        )
    if not row:
        # У юзера уже есть referrer (или юзер не найден) — не перезаписываем (антифрод).
        logger.info(
            "User already has referrer or not found, skipping merge",
            referred=referred_tg_id,
            attempted=referrer_tg_id,
        )
        return False

    # Синхронизируем кэш: обновляем закешированного пользователя (DB уже записал).
    user = await service_data.users.get_data(referred_tg_id, conn=pool)
    if user:
        user.referral_id = referrer_tg_id
        await cache.users.set(CacheKeyManager.user(referred_tg_id), user)

    # ReferralRedemption (UNIQUE(referred_tg_id) защищает от дублей)
    try:
        redemption = ReferralRedemption(
            referral_link_id=link.id,
            referred_tg_id=referred_tg_id,
        )
        await service_data.data_service.referral_redemptions.create(
            pool, **redemption.to_dict()
        )
        logger.info(
            "Referral merged via landing cookie",
            referrer=referrer_tg_id,
            referred=referred_tg_id,
        )
    except asyncpg.UniqueViolationError:
        # Повторный claim/mark для того же tg_id — идемпотентно.
        logger.info(
            "Referral redemption already exists, skipping",
            referred=referred_tg_id,
        )
    except Exception as e:
        logger.error(
            "Failed to insert referral_redemption",
            referred=referred_tg_id,
            error=str(e),
        )

    return True


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


def _extract_vless_url(subscription_url: str) -> Optional[str]:
    """Скачивает subscription URL и извлекает первую vless:// строку.

    Поддерживает plain-text и base64-encoded subscription-ответы.
    Повторяет попытки при кратковременных сетевых/3x-UI сбоях.
    Результат кешируется в памяти 5 минут по subscription URL.
    Возвращает None, если скачать не удалось или vless-конфиг не найден.
    """
    import base64
    import time
    import urllib.request

    now = time.time()
    cached = _VLESS_CACHE.get(subscription_url)
    if cached is not None:
        vless_url, expires_at = cached
        if expires_at > now:
            return vless_url
        _VLESS_CACHE.pop(subscription_url, None)

    last_error = None
    body: Optional[bytes] = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(subscription_url, timeout=8) as response:
                body = response.read()
            break
        except Exception as e:
            last_error = e
            logger.warning(
                "Попытка скачать subscription URL не удалась",
                url=subscription_url,
                attempt=attempt,
                error=str(e),
            )
            if attempt < 3:
                time.sleep(0.5 * attempt)
            else:
                logger.warning(
                    "Не удалось скачать subscription URL для vless",
                    url=subscription_url,
                    error=str(last_error),
                )
                return None

    result: Optional[str] = None

    # Пробуем plain text
    for encoding in ("utf-8", "utf-8-sig"):
        try:
            text = body.decode(encoding, errors="ignore")
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("vless://"):
                    result = line
                    break
        except Exception:
            continue
        if result:
            break

    # Пробуем base64 (Happ/Sing-box часто отдают подписку в base64)
    if result is None:
        try:
            decoded = base64.b64decode(body, validate=True).decode("utf-8", errors="ignore")
            for line in decoded.splitlines():
                line = line.strip()
                if line.startswith("vless://"):
                    result = line
                    break
        except Exception:
            pass

    if result is not None:
        _VLESS_CACHE[subscription_url] = (result, now + _VLESS_CACHE_TTL_SECONDS)

    return result


def _build_deep_links(key_value: str, landing_uid: str) -> tuple[str, str]:
    """Формирует deep-link в Happ (открыть и импортировать) и в Telegram-бота.

    Happ-клиент принимает subscription URL через `happ://add/<url>` **без**
    URL-кодирования — см. апстрим 3x-ui (PR MHSanaei/3x-ui#3863,
    «Fix DeepLink for Happ, remove encoding URL»). Любое кодирование или
    иные действия (happ://import/, извлечение vless://) Happ отвергает
    ошибкой «Неизвестное действие DeepLink».

    В проде key.key — это subscription URL (http(s)://…), отдаём его как есть.
    """
    deep_link_happ = f"happ://add/{key_value}"

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
    # Сначала ищем в кеше (быстро) — CacheService.keys.all() асинхронен.
    all_keys = []
    if hasattr(service_data.cache_service.keys, "all"):
        all_keys = await service_data.cache_service.keys.all()
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
# POST /landing/set-ref-cookie
# =============================================================================
@router.post("/set-ref-cookie")
async def set_ref_cookie(
    response: Response,
    payload: SetRefCookieRequest,
    service_data: ServiceDataModel = Depends(get_service_data),
):
    """Поставить HMAC-куку ``tg_ref`` для реферера, зашедшего на лендинг с ?ref=….

    Вызывается JS-стороной лендинга при первом заходе. Валидирует формат токена
    и его существование в ``referral_links`` (cache → DB), затем ставит куку.

    Безопасно вызывать без аутентификации (verify_bot_secret уже стоит на роутере):
    кука бесполезна без реальной записи в БД, а в ``claim_landing_key`` /
    ``mark_converted`` self-referral и overwrite защиты.
    """
    token = payload.ref_token
    if not _is_valid_ref_format(token):
        raise HTTPException(status_code=400, detail="invalid ref_token format")

    # Проверяем существование токена — не ставим куку на мусор.
    link = await service_data.referral_links.get_by(token=token)
    if not link:
        raise HTTPException(status_code=404, detail="ref_token not found")

    response.set_cookie(
        key=TG_REF_COOKIE_NAME,
        value=_sign_ref_cookie(token),
        max_age=TG_REF_COOKIE_MAX_AGE,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    logger.info(
        "tg_ref cookie set via landing",
        token=token,
        referrer=link.referrer_tg_id,
    )
    return {"ok": True}


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
        landing_uid=landing_uid,
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

    # Ключ уже привязан к реальному tg_id (claim выполнен) → юзер дошёл до бота
    # и забрал ключ. mark-converted (существующий юзер) НЕ переносит tg_id, поэтому
    # для него ключ остаётся на псевдо-tg_id (<0) и лендинг продолжает показывать
    # активный 24ч ключ — как и требуется («ключ не отключается до истечения 24ч»).
    if key_obj.converted_tg_id is not None and key_obj.tg_id and key_obj.tg_id > 0:
        return LandingStateResponse(state="converted")

    now_ms = int(time.time() * 1000)
    expiry_ms = int(key_obj.expiry_time or 0)

    # Определяем состояние
    if expiry_ms <= now_ms:
        return LandingStateResponse(state="expired")

    remaining_seconds = (expiry_ms - now_ms) // 1000

    deep_link_happ, deep_link_bot = _build_deep_links(key_obj.key, landing_uid)
    bot_url = f"{LANDING_BOT_LINK_PREFIX}{settings.bot_name or 'TolkoDlyaSv0ih_Bot'}"

    state = "expiring" if remaining_seconds < EXPIRING_THRESHOLD_HOURS * 3600 else "active"

    # already_registered: mark-converted (существующий юзер) или already_claimed_other
    # — converted_tg_id выставлен, но tg_id остался псевдо (<0). 24ч-ключ живёт,
    # но бесплатное продление по claim недоступно → фронт показывает баннер.
    already_registered = key_obj.converted_tg_id is not None

    return LandingStateResponse(
        state=state,
        key_value=key_obj.key,
        expires_at_ms=expiry_ms,
        remaining_seconds=remaining_seconds,
        deep_link_happ=deep_link_happ,
        deep_link_bot=deep_link_bot,
        bot_url=bot_url,
        already_registered=already_registered,
        landing_uid=landing_uid,
    )


# =============================================================================
# POST /landing/mark-converted (admin endpoint — вызывается ботом)
# =============================================================================
class MarkConvertedRequest(BaseModel):
    tg_id: int
    ref_token: Optional[str] = None


@router.post("/mark-converted/{landing_uid}")
async def mark_converted(
    landing_uid: str,
    body: MarkConvertedRequest,
    tg_ref: Optional[str] = Cookie(None),
    pool: asyncpg.Pool = Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
    cache: CacheService = Depends(get_cache),
):
    """Проставить converted_tg_id для ключа, найденного по landing_uid.

    Вызывается ботом, когда СУЩЕСТВУЮЩИЙ юзер приходит по /start landing_<uid>.
    Временный ключ НЕ отключается и НЕ переносится на реальный tg_id —
    продолжает жить до 24ч на псевдо-tg_id.
    """
    key_obj = await _get_key_by_landing_uid(service_data, pool, landing_uid)
    if not key_obj:
        raise HTTPException(status_code=404, detail="Landing key not found")

    # Идемпотентность: тот же юзер повторно кликнул по ссылке
    if key_obj.converted_tg_id == body.tg_id:
        # Попробуем merge referral (если ещё не было) — может быть повторный
        # клик с уже-выставленным converted_tg_id, но referral_id не успел записаться.
        await _attach_referral_if_any(pool, service_data, cache, tg_ref, body.tg_id, raw_ref_token=body.ref_token)
        return {"ok": True, "already": True, "email": key_obj.email}

    # Ключ уже привязан к другому аккаунту — НЕ перезаписываем (защита от гонок и
    # повторного использования одной ссылки разными юзерами).
    if key_obj.converted_tg_id is not None and key_obj.converted_tg_id != body.tg_id:
        logger.warning(
            "Landing key уже привязан к другому tg_id",
            landing_uid=landing_uid,
            current=key_obj.converted_tg_id,
            new=body.tg_id,
        )
        return {"ok": False, "status": "already_claimed_other", "email": key_obj.email}

    key_obj.converted_tg_id = body.tg_id
    # НЕ удаляем из x-ui — ключ продолжает работать до 24ч
    await service_data.keys.update(pool, key_obj, search_data={"email": key_obj.email})
    await cache.keys.set(CacheKeyManager.key(key_obj.email), key_obj)

    # Merge referral из tg_ref куки или ref_token в теле (бот пробрасывает из deeplink).
    await _attach_referral_if_any(pool, service_data, cache, tg_ref, body.tg_id, raw_ref_token=body.ref_token)

    logger.info(
        "Landing key помечен как converted",
        landing_uid=landing_uid,
        tg_id=body.tg_id,
        email=key_obj.email,
    )

    return {"ok": True, "email": key_obj.email}


# =============================================================================
# POST /landing/claim/{landing_uid} — вызывается ботом для НОВОГО юзера
# =============================================================================
class ClaimRequest(BaseModel):
    tg_id: int
    ref_token: Optional[str] = None


@router.post("/claim/{landing_uid}")
async def claim_key(
    landing_uid: str,
    body: ClaimRequest,
    tg_ref: Optional[str] = Cookie(None),
    pool: asyncpg.Pool = Depends(get_pool),
    service_data: ServiceDataModel = Depends(get_service_data),
    cache: CacheService = Depends(get_cache),
):
    """Привязать 24ч лендинг-ключ к реальному tg_id и продлить по trial-тарифу.

    Вызывается ботом, когда НОВЫЙ юзер приходит по /start landing_<uid> (бот
    сначала авто-регистрирует юзера, затем вызывает этот эндпоинт).

    - Переносит владельца: tg_id pseudo → реальный (тот же ключ, что в Happ).
    - Продлевает срок на trial-период (7 дней) от текущего expiry (или от now,
      если ключ уже истёк).
    - Ставит tariff_id = trial, trial=1, converted_tg_id = реальный tg_id.
    - Выравнивает user.server_id с сервером ключа (иначе продление из бота
      сломается — /keys/{email}/renew берёт сервер из user.server_id).
    - При наличии куки tg_ref — мержит referral_id (см. _attach_referral_if_any).
    """
    key_obj = await _get_key_by_landing_uid(service_data, pool, landing_uid)
    if not key_obj:
        raise HTTPException(status_code=404, detail="Landing key not found")

    # Идемпотентность: тот же юзер повторно кликнул — отдаём его ключ
    if key_obj.converted_tg_id == body.tg_id:
        # Повторный клик с уже-выставленным converted_tg_id: попробуем merge
        # referral, если ещё не было (tg_ref мог быть поставлен после первого claim).
        await _attach_referral_if_any(pool, service_data, cache, tg_ref, body.tg_id, raw_ref_token=body.ref_token)
        return {
            "status": "already_claimed",
            "email": key_obj.email,
            "key_value": key_obj.key,
            "expires_at_ms": int(key_obj.expiry_time or 0),
        }

    # Ключ уже привязан к другому аккаунту — не отдаём
    if key_obj.converted_tg_id is not None and key_obj.converted_tg_id != body.tg_id:
        logger.warning(
            "Landing key уже привязан к другому tg_id",
            landing_uid=landing_uid,
            current=key_obj.converted_tg_id,
            new=body.tg_id,
        )
        return {"status": "already_claimed_other", "email": key_obj.email}

    # Trial-тариф (DEFAULT_PRICING_PLAN, обычно id=10, period=7 дней)
    trial_tariff = await service_data.tariffs.get_data(
        int(DEFAULT_PRICING_PLAN), conn=pool
    )
    if not trial_tariff:
        raise HTTPException(status_code=404, detail="Trial tariff not found")

    # Юзер должен быть зарегистрирован ботом заранее
    user = await service_data.users.get_data(body.tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not registered")

    # Апгрейд того же клиента: attach [11,12], trial expiry, перенос tg_id
    # на реальный. Happ-URL (key.key) сохраняется. converted_tg_id ставится
    # до вызова — upgrade_landing_key читает его, чтобы перенести tg_id
    # (transfer_tg). При провале откатываем, иначе key_obj (живой объект
    # кеша на cache-hit) остался бы «привязанным» и повторный клик упёрся
    # бы в already_claimed без рабочего ключа.
    key_obj.converted_tg_id = body.tg_id
    loading = LoadingService(cache=cache, data_service=DataService(), pool=pool)
    xui = XUISession(model_service=service_data, loading=loading)
    new_expiry = ExpiryCalculator().key_duration_new_key(trial_tariff.period, 1)
    upgraded = await upgrade_landing_key(
        xui_session=xui,
        model_data=service_data,
        cache=cache,
        pool=pool,
        key=key_obj,
        tariff=trial_tariff,
        new_expiry=new_expiry,
        transfer_tg=True,
    )
    if not upgraded:
        key_obj.converted_tg_id = None  # откат, чтобы повторный клик мог retry
        raise HTTPException(status_code=500, detail="Failed to upgrade landing key")

    # trial=1 (как в /keys/trial)
    await TrialService(service_data).installation_trial(body.tg_id, pool, trial=1)

    # Выровнять server_id с сервером ключа, чтобы продление из бота работало
    if user.server_id != settings.xui_server_id:
        user.server_id = settings.xui_server_id
        await service_data.users.update(pool, user, {"tg_id": body.tg_id})
        await cache.users.set(CacheKeyManager.user(body.tg_id), user)

    # Merge referral из tg_ref куки или ref_token в теле (бот пробрасывает из deeplink).
    # Делаем ПОСЛЕ installation_trial, чтобы в логах redemption шёл после trial=1.
    # Ошибка merge НЕ откатывает claim (бонус-движок защищён check_referral=FALSE).
    await _attach_referral_if_any(pool, service_data, cache, tg_ref, body.tg_id, raw_ref_token=body.ref_token)

    logger.info(
        "Landing key привязан к юзеру и апгрейдирован (trial)",
        landing_uid=landing_uid, tg_id=body.tg_id, email=upgraded.email,
        new_expiry_ms=upgraded.expiry_time,
    )

    return {
        "status": "claimed",
        "email": upgraded.email,
        "key_value": upgraded.key,
        "expires_at_ms": int(upgraded.expiry_time or 0),
    }
