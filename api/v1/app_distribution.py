"""Публичная раздача Android-приложения (dlay.svoih) по стабильной ссылке.

APK на сервер попадает вручную при деплое (rsync/scp в static/downloads/,
см. CLAUDE.md) — здесь нет ни загрузки файла через API, ни версионирования.
Эндпоинт просто редиректит на текущий файл, чтобы публично раздаваемая
ссылка не зависела от имени/версии конкретного APK.
"""
from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse

from app.rate_limit import rate_limit

router = APIRouter(prefix="/public/app", tags=["app-distribution"])

ANDROID_APK_PATH = "/static/downloads/dlay-svoih.apk"


@router.get(
    "/android",
    dependencies=[Depends(rate_limit("public-app-android", times=60, seconds=60))],
)
async def download_android_app() -> RedirectResponse:
    return RedirectResponse(url=ANDROID_APK_PATH)
