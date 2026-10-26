from fastapi import APIRouter

from api.v1 import (
    admin,
    admin_promo,
    admin_session,
    app_distribution,
    auth,
    keys,
    landing,
    miniapp,
    mobile_mvp,
    payments,
    public,
    renew_link,
    tariffs,
    users,
    web,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(tariffs.router)
api_router.include_router(users.router)
api_router.include_router(keys.router)
api_router.include_router(payments.router)
api_router.include_router(admin_session.router)
api_router.include_router(admin.router)
api_router.include_router(admin.destructive_router)
api_router.include_router(admin_promo.router)
api_router.include_router(admin_promo.destructive_router)
api_router.include_router(landing.router)
api_router.include_router(mobile_mvp.router)
api_router.include_router(miniapp.router)
api_router.include_router(web.router)
api_router.include_router(web.bot_router)
api_router.include_router(renew_link.router)
api_router.include_router(public.router)
api_router.include_router(app_distribution.router)
