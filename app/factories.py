import asyncpg
from typing import Optional

from client import XUISession
from database.service import DataService
from services.cache.loader import LoadingService
from services.cache.service import CacheService
from services.core.connect_module.repositories.form_data import FormConnectionData
from services.core.data.service import ServiceDataModel
from services.core.keys.utils.calculator import ExpiryCalculator
from services.core.keys.utils.create_key import CreateKey
from services.core.keys.utils.formtion import FormationKey
from services.core.keys.utils.renewal import KeyRenewal
from services.core.keys.utils.reset import KeyResetter
from services.core.keys.utils.updating import KeyUpdater
from services.core.payment.creation_service import KeyCreationService
from services.core.payment.processor import PaymentProcessor
from services.core.payment.renewal_service import KeyRenewalService
from services.core.payment.router import PaymentRouter
from services.core.notifications.protocols import INotifier


def build_key_services(
    pool: asyncpg.Pool,
    service_data: ServiceDataModel,
    cache: CacheService,
    data_service: DataService,
) -> tuple:
    """
    Builds key services (CreateKey, KeyRenewal, XUISession).

    Returns:
        (create_key, key_renewal, xui) tuple
    """
    expiry = ExpiryCalculator()
    loading = LoadingService(cache=cache, data_service=data_service, pool=pool)
    xui = XUISession(model_service=service_data, loading=loading)

    connected_data = FormConnectionData(cache=cache, model_data=service_data)
    connected_data.set_pool(pool)
    connected_data.set_xui_session(xui)
    formation = FormationKey(cache=cache, connected_data=connected_data, expiry=expiry)
    create_key = CreateKey(
        model_data=service_data,
        xui_session=xui,
        expiry=expiry,
        formation=formation,
    )

    updater = KeyUpdater(expiry_calculator=expiry)
    resetter = KeyResetter(cache_service=cache)
    key_renewal = KeyRenewal(
        model_data=service_data,
        xui_session=xui,
        refresh_key=updater,
        resetter=resetter,
    )

    return (create_key, key_renewal, xui)


def build_payment_router(
    pool: asyncpg.Pool,
    service_data: ServiceDataModel,
    cache: CacheService,
    data_service: DataService,
    notifier: Optional[INotifier] = None,
) -> PaymentRouter:
    """
    Build payment router with all dependencies.

    Args:
        pool: Database connection pool
        service_data: Service data model
        cache: Cache service
        data_service: Data service
        notifier: Optional notification service for sending user messages.
                  If None, services will not send notifications.

    Returns:
        PaymentRouter configured with all services
    """
    create_key, key_renewal, xui = build_key_services(
        pool, service_data, cache, data_service
    )

    processor = PaymentProcessor(conn=pool, model_service=service_data, cache=cache)
    creation_svc = KeyCreationService(
        processor=processor, create_key=create_key, notifier=notifier
    )
    renewal_svc = KeyRenewalService(
        processor=processor, key_manager=key_renewal, notifier=notifier
    )

    return PaymentRouter(
        processor=processor,
        creation_service=creation_svc,
        renewal_service=renewal_svc,
    )
