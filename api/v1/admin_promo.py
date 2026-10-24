"""Промокоды в админке (/api/v1/admin/promo-codes).

Чтение — `router` (как остальная админка: ключ бота или сессия админа),
создание и выключение — `destructive_router` (только админ, с аудитом).
"""
from dataclasses import asdict

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.auth import AdminPrincipal, verify_admin_actor, verify_admin_or_bot
from app.dependencies import get_pool
from app.factories import build_promo_code_repository
from app.schemas.promo_codes import (
    AdminPromoCodeCreateRequest,
    AdminPromoCodeResponse,
    AdminPromoCodesListResponse,
    AdminPromoCodeStatsResponse,
    AdminPromoCodeUpdateRequest,
    AdminWinbackStatsResponse,
)
from config import settings
from services.admin_audit import AuditLogger
from services.core.promo_codes.admin import InvalidPromoCode, NewPromoCode, PromoCodeAdmin
from services.core.promo_codes.repository import DuplicatePromoCode
from services.core.promo_codes.winback import WinbackPolicy, WinbackRepository

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(verify_admin_or_bot)])
destructive_router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(verify_admin_actor)])


def _admin(pool: asyncpg.Pool) -> PromoCodeAdmin:
    return PromoCodeAdmin(build_promo_code_repository(pool), max_percent=settings.promo_max_percent)


@router.get("/promo-codes", response_model=AdminPromoCodesListResponse)
async def admin_list_promo_codes(
    response: Response,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    pool: asyncpg.Pool = Depends(get_pool),
):
    """Акционные коды, новые сверху, со счётчиками активаций. Личные коды не показываются."""
    items, total = await _admin(pool).list(limit, offset)
    response.headers["X-Total-Count"] = str(total)
    return {"promo_codes": [AdminPromoCodeResponse.build(promo, usage) for promo, usage in items]}


@router.get("/promo-codes/{promo_id}/stats", response_model=AdminPromoCodeStatsResponse)
async def admin_promo_code_stats(
    promo_id: int,
    window_days: int = Query(14, ge=1, le=90, description="Сколько дней до начала и после конца сравнивать"),
    pool: asyncpg.Pool = Depends(get_pool),
):
    found = await _admin(pool).stats(promo_id, window_days)
    if found is None:
        raise HTTPException(status_code=404, detail="Promo code not found")
    promo, stats = found
    return {"promo_code": AdminPromoCodeResponse.build(promo), **asdict(stats)}


@router.get("/winback/stats", response_model=AdminWinbackStatsResponse)
async def admin_winback_stats(
    days: int = Query(30, ge=1, le=365, description="Предложения за столько последних дней"),
    pool: asyncpg.Pool = Depends(get_pool),
):
    """Акция возврата: сколько вернулись со скидкой и в контрольной группе без неё."""
    policy = WinbackPolicy.from_settings(settings)
    stats = await WinbackRepository(pool).stats(days, policy.attribution_days)
    return {
        "enabled": policy.enabled,
        "discount_percent": policy.discount_percent,
        "valid_hours": policy.valid_hours,
        "min_days": policy.min_days,
        "max_days": policy.max_days,
        "control_percent": policy.control_percent,
        **asdict(stats),
    }


@destructive_router.post("/promo-codes", response_model=AdminPromoCodeResponse, status_code=201)
async def admin_create_promo_code(
    body: AdminPromoCodeCreateRequest,
    pool: asyncpg.Pool = Depends(get_pool),
    principal: AdminPrincipal = Depends(verify_admin_actor),
):
    try:
        promo = await _admin(pool).create(NewPromoCode(**body.model_dump()), created_by=principal.admin_tg_id)
    except InvalidPromoCode as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except DuplicatePromoCode as e:
        raise HTTPException(status_code=409, detail="Такой код уже есть") from e
    await AuditLogger(pool).record(principal.admin_tg_id, "create_promo_code", promo.code)
    return AdminPromoCodeResponse.build(promo)


@destructive_router.patch("/promo-codes/{promo_id}", response_model=AdminPromoCodeResponse)
async def admin_update_promo_code(
    promo_id: int,
    body: AdminPromoCodeUpdateRequest,
    pool: asyncpg.Pool = Depends(get_pool),
    principal: AdminPrincipal = Depends(verify_admin_actor),
):
    """Включить или выключить код. Выключенный код сразу перестаёт проходить проверку,
    уже выставленные счета со скидкой остаются в силе."""
    promo = await _admin(pool).set_active(promo_id, body.is_active)
    if promo is None:
        raise HTTPException(status_code=404, detail="Promo code not found")
    action = "enable_promo_code" if body.is_active else "disable_promo_code"
    await AuditLogger(pool).record(principal.admin_tg_id, action, promo.code)
    return AdminPromoCodeResponse.build(promo)
