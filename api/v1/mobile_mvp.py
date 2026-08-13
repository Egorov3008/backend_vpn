"""
Публичный MVP-эндпоинт "общего" VPN-конфига для Android-приложения.

Единый shared VPN-ключ (без аккаунтов, без per-user состояния), выдаваемый
всем инсталляциям приложения, аутентифицированным только статическим
секретом в заголовке ``X-App-Secret``. Сам shared-ключ провижинится
административно вне этого кода (см. settings.mvp_shared_key_email) — этот
модуль лишь находит его и отдаёт vless-конфиг.

Намеренно НЕ импортирует ничего из ``api/v1/landing.py`` (см. task-2-brief):
download/extract-логика продублирована здесь независимо, чтобы этот MVP-срез
не нёс риска для существующей, протестированной фичи лендинга.
"""
from __future__ import annotations

import asyncio
import base64
import time
import urllib.request
from typing import Optional

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException

from app.dependencies import get_pool, get_service_data
from config import settings
from logger import logger
from services.cache.key_manager import CacheKeyManager
from services.core.data.service import ServiceDataModel

router = APIRouter(prefix="/mobile", tags=["mobile-mvp"])

# Единственный shared-ключ => идентичный ответ для всех вызывающих. Кеш в
# памяти по subscription URL (зеркалит паттерн landing.py::_VLESS_CACHE, но
# не импортируется оттуда — см. docstring модуля) схлопывает нагрузку от
# множества инсталляций 3x-UI-приложения примерно до одного скачивания раз в
# 5 минут. Кешируются только успешные результаты — неудачное скачивание не
# кешируется, чтобы следующий запрос сразу повторил попытку.
_VLESS_CACHE: dict[str, tuple[str, float]] = {}
_VLESS_CACHE_TTL_SECONDS = 300


def _download_and_extract_vless(subscription_url: str) -> Optional[str]:
    """Скачивает subscription URL и извлекает первую vless:// строку.

    Поддерживает plain-text и base64-encoded subscription-ответы.
    Повторяет попытки при кратковременных сетевых/3x-UI сбоях.
    Возвращает None, если скачать не удалось или vless-конфиг не найден.

    Независимая копия логики ``landing.py::_extract_vless_url`` — с тем же
    паттерном in-memory TTL-кеша (см. ``_VLESS_CACHE`` выше), но без импорта
    из landing.py (см. docstring модуля).
    """
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

    # Пробуем base64 (Happ/Sing-box часто отдают подписку в base64).
    # .strip() до decode: реальные subscription-серверы часто дописывают
    # trailing "\n", а base64.b64decode(..., validate=True) падает на ЛЮБОМ
    # символе вне base64-алфавита, включая whitespace по краям.
    if result is None:
        try:
            decoded = base64.b64decode(
                body.strip(), validate=True
            ).decode("utf-8", errors="ignore")
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


async def verify_app_secret(x_app_secret: Optional[str] = Header(None)) -> None:
    """Статический header-secret для мобильного MVP-эндпоинта.

    Паттерн зеркалит ``app/auth.py::verify_bot_secret``.
    """
    if not x_app_secret or x_app_secret != settings.mvp_app_secret:
        raise HTTPException(status_code=401, detail="Invalid app secret")


@router.get("/shared-config", dependencies=[Depends(verify_app_secret)])
async def get_shared_config(
    service_data: ServiceDataModel = Depends(get_service_data),
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict:
    """Вернуть общий (shared) VPN-конфиг для всех Android-инсталляций.

    - 500, если shared-ключ не настроен на сервере или не найден в БД
      (ошибка деплоя/провижининга, не запроса пользователя).
    - 502, если скачать/распарсить subscription-конфиг не удалось
      (upstream/сетевая ошибка).
    """
    if not settings.mvp_shared_key_email:
        raise HTTPException(status_code=500, detail="Shared key not configured")

    key = await service_data.keys.get_data(settings.mvp_shared_key_email)
    if not key:
        key = await service_data.data_service.keys.get(
            pool, email=settings.mvp_shared_key_email
        )
        if key:
            await service_data.cache_service.keys.set(
                CacheKeyManager.key(settings.mvp_shared_key_email), key
            )
    if not key:
        raise HTTPException(status_code=500, detail="Shared key not configured")

    logger.warning(
        "Serving MVP shared VPN config",
        email=key.email,
        tg_id=key.tg_id,
        limit_ip=key.limit_ip,
    )

    # Скачивание/парсинг subscription — блокирующий сетевой I/O (urlopen +
    # time.sleep между ретраями, до ~25с в худшем случае). Выносим в поток,
    # иначе это замораживает весь event loop процесса (платёжные вебхуки,
    # bot API, scheduler) на время скачивания.
    vless_uri = await asyncio.to_thread(_download_and_extract_vless, key.key)
    if vless_uri is None:
        raise HTTPException(status_code=502, detail="Failed to retrieve VPN config")

    return {"vless_uri": vless_uri, "expiry_time": key.expiry_time}
