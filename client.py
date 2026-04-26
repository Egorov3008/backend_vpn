import asyncio
import time
from datetime import datetime, timedelta
from multiprocessing import AuthenticationError
from typing import Optional, Any

from py3xui import AsyncApi, Client, Inbound
from tenacity import stop, wait, retry, retry_if_exception, RetryCallState

from core.utils import filter_by_method_signature

from logger import logger
from models import Server, Key
from services.cache import LoadingService
from services.core.data.service import ServiceDataModel
from services.metrics.registry import (
    xui_api_calls_total,
    xui_api_errors_total,
    xui_api_duration,
    xui_api_retries_total,
)

# Метрики специально для login
xui_login_duration = xui_api_duration.labels(method="login")
xui_login_calls_total = xui_api_calls_total.labels(method="login")


class XUIRetryPolicy:
    """Политика повторных попыток для XUI API"""

    @staticmethod
    def is_retryable_exception(exception: Exception) -> bool:
        """Определяет, можно ли повторять попытку для данного исключения"""
        retryable_exceptions = (
            ConnectionError,
            TimeoutError,
            asyncio.TimeoutError,
            ConnectionRefusedError,
            ConnectionResetError,
        )

        non_retryable_exceptions = (
            AuthenticationError,
            ValueError,
            KeyError,
        )

        # Не повторять для фатальных ошибок
        if isinstance(exception, non_retryable_exceptions):
            return False

        # Повторять для сетевых и временных ошибок
        if isinstance(exception, retryable_exceptions):
            return True

        # Для остальных - не повторять по умолчанию
        return False

    @staticmethod
    def before_sleep_callback(retry_state: RetryCallState):
        """Callback перед ожиданием следующей попытки"""
        # Определяем имя метода из fn
        method_name = "unknown"
        if retry_state.fn:
            method_name = getattr(retry_state.fn, "__name__", "unknown")
        xui_api_retries_total.labels(method=method_name).inc()

        if retry_state.outcome and retry_state.outcome.failed:
            exception = retry_state.outcome.exception()
            logger.warning(
                f"Попытка {retry_state.attempt_number} failed: {exception}. "
                f"Следующая попытка через {retry_state.next_action.sleep:.1f} сек"
            )
        else:
            logger.info(
                f"Попытка {retry_state.attempt_number}. "
                f"Следующая попытка через {retry_state.next_action.sleep:.1f} сек"
            )


xui_police = XUIRetryPolicy()


class XUISession:
    """Единая сессия для работы с XUI панелью"""

    def __init__(
        self, model_service: ServiceDataModel, loading: LoadingService,
        login_timeout: float = 15.0, login_max_retries: int = 1
    ) -> None:
        self.server_id = 2
        self._is_authenticated = False
        self.server = None
        self.xui = None
        self.server_data = model_service.servers
        self.loading = loading
        self._initialized = False
        self.login_timeout = login_timeout  # Таймаут для login() в секундах
        self.login_max_retries = login_max_retries  # Максимум попыток для login()

    async def _ensure_initialized(self):
        """Ленивая инициализация сервера"""
        if not self._initialized:
            await self.server_init()
            self._initialized = True

    async def server_init(self):
        self.server: Optional[Server] = await self.server_data.get_data(self.server_id)
        if not self.server:
            await self.loading.load_server()
            self.server: Optional[Server] = await self.server_data.get_data(
                self.server_id
            )
            logger.debug(
                "Сервер загружен из кеша", server=self.server, server_id=self.server_id
            )

        self.xui = AsyncApi(
            host=self.server.api_url,
            username=self.server.login,
            password=self.server.password,
        )
        # Увеличиваем timeout для медленной панели
        if hasattr(self.xui, 'client') and hasattr(self.xui.client, 'timeout'):
            self.xui.client.timeout = 30.0

    # Все методы (ensure_auth, add_client и т.д.) должны начинаться с:
    async def ensure_auth(self):
        await self._ensure_initialized()  # <- гарантируем инициализацию
        if not self._is_authenticated:
            # Устанавливаем количество retries для login
            self.xui.client.max_retries = self.login_max_retries
            
            t0 = time.monotonic()
            xui_login_calls_total.inc()
            try:
                # Оборачиваем login() с таймаутом (должен быть >= httpx timeout)
                await asyncio.wait_for(
                    self.xui.login(),
                    timeout=max(self.login_timeout, 35.0)
                )
                login_duration = time.monotonic() - t0
                self._is_authenticated = True
                
                # Записываем метрику времени выполнения
                xui_login_duration.observe(login_duration)
                
                # Логирование времени выполнения
                if login_duration > 5.0:
                    logger.warning(
                        "Login выполнен медленно",
                        duration_sec=round(login_duration, 2),
                        timeout=self.login_timeout
                    )
                else:
                    logger.debug(
                        "Login выполнен успешно",
                        duration_sec=round(login_duration, 3)
                    )
                    
            except asyncio.TimeoutError:
                xui_api_errors_total.labels(method="login", error_type="TimeoutError").inc()
                logger.error(
                    "Превышен таймаут login",
                    timeout=self.login_timeout,
                    host=self.server.api_url
                )
                raise TimeoutError(
                    f"Login превысил таймаут {self.login_timeout}сек"
                )
            except Exception as e:
                xui_api_errors_total.labels(method="login", error_type=type(e).__name__).inc()
                logger.error(
                    "Ошибка при login",
                    error_type=type(e).__name__,
                    error_message=str(e),
                    exc_info=True
                )
                raise

    @retry(
        stop=stop.stop_after_attempt(3),
        wait=wait.wait_fixed(2),  # Фиксированная задержка для операций с клиентами
        retry=retry_if_exception(XUIRetryPolicy.is_retryable_exception),
        reraise=True,
    )
    @filter_by_method_signature
    async def add_client(
        self,
        client_id: str,
        email: str,
        tg_id: int,
        limit_ip: int,
        inbound_id: int,
        expiry_time: int,
        total_gb: int,
        enable: bool = True,
        flow: str = "xtls-rprx-vision",
    ) -> Any:
        """Добавляет клиента на сервер через 3x-ui"""

        await self.ensure_auth()
        t0 = time.monotonic()
        xui_api_calls_total.labels(method="add_client").inc()
        try:
            client = Client(
                id=client_id,
                email=email,
                limit_ip=limit_ip,
                expiry_time=expiry_time,
                enable=enable,
                inbound_id=inbound_id,
                tg_id=tg_id,
                total_gb=total_gb,
                sub_id=email,
                flow=flow,
            )

            await self.xui.client.add(inbound_id, [client])
            logger.info(
                "Клиент успешно добавлен с ID", email=email, client_id=client_id
            )
            return True

        except Exception as e:
            xui_api_errors_total.labels(
                method="add_client", error_type=type(e).__name__
            ).inc()
            logger.error(
                "Ошибка при добавлении клиента",
                email=email,
                error_type=type(e).__name__,
                error_message=str(e),
                exc_info=True,
            )
            return False
        finally:
            xui_api_duration.labels(method="add_client").observe(
                time.monotonic() - t0
            )

    async def extend_client_key(
        self,
        key_details: Key,
    ) -> bool:
        """Обновляет срок действия ключа клиента"""
        t0 = time.monotonic()
        xui_api_calls_total.labels(method="extend_client_key").inc()
        try:
            await self.ensure_auth()

            client = await self.xui.client.get_by_email(key_details.email)
            if not client or not client.id:
                logger.warning("Клиент не найден", email=key_details.email)
                return False

            # Расчет нового времени expiry
            expiry_datetime = datetime.fromtimestamp(key_details.expiry_time / 1000)
            logger.debug(
                "Проверка присваемого времени",
                expiry_time=key_details.expiry_time,
                expiry_datetime=expiry_datetime,
            )
            # Обновление клиента
            client.inbound_id = key_details.inbound_id
            client.id = key_details.client_id
            client.tg_id = key_details.tg_id
            client.total_gb = key_details.total_gb
            client.limit_ip = int(key_details.limit_ip) if key_details.limit_ip is not None else 1
            client.expiry_time = key_details.expiry_time
            client.flow = "xtls-rprx-vision"
            client.sub_id = key_details.email
            client.enable = True
            logger.debug("Сформированный клиент", client=client)
            await self.xui.client.update(client.id, client)
            await self.xui.client.reset_stats(client.inbound_id, key_details.email)

            logger.info("Ключ клиента продлён", email=key_details.email)
            return True

        except Exception as e:
            xui_api_errors_total.labels(
                method="extend_client_key", error_type=type(e).__name__
            ).inc()
            logger.error(
                "Ошибка при продлении ключа ",
                email=key_details.email,
                error_type=type(e).__name__,
                error_message=str(e),
                exc_info=True,
            )
            return False
        finally:
            xui_api_duration.labels(method="extend_client_key").observe(
                time.monotonic() - t0
            )

    @filter_by_method_signature
    async def delete_client(self, email: str, inbound_id: int, client_id: str) -> bool:
        """Удаляет клиента с сервера"""
        t0 = time.monotonic()
        xui_api_calls_total.labels(method="delete_client").inc()
        try:
            await self.ensure_auth()

            client = await self.xui.client.get_by_email(email)
            if not client:
                logger.warning("Клиент не найден", email=email)
                return False

            await self.xui.client.delete(inbound_id, client_id)
            logger.info("Клиент удалён", email=email)
            return True

        except Exception as e:
            xui_api_errors_total.labels(
                method="delete_client", error_type=type(e).__name__
            ).inc()
            logger.error(
                "Ошибка при удалении клиента",
                email=email,
                error_type=type(e).__name__,
                error_message=str(e),
                exc_info=True,
            )
            return False
        finally:
            xui_api_duration.labels(method="delete_client").observe(
                time.monotonic() - t0
            )

    async def get_inbounds(self) -> list[Inbound]:
        """Получает словарь подключений панели"""
        t0 = time.monotonic()
        xui_api_calls_total.labels(method="get_inbounds").inc()
        try:
            await self.ensure_auth()
            inbounds = await self.xui.inbound.get_list()
            if inbounds:
                return inbounds
            return []

        except Exception as e:
            xui_api_errors_total.labels(
                method="get_inbounds", error_type=type(e).__name__
            ).inc()
            logger.error(
                "Ошибка при получении списка подключений",
                error_type=type(e).__name__,
                error_message=str(e)
            )
            return []
        finally:
            xui_api_duration.labels(method="get_inbounds").observe(
                time.monotonic() - t0
            )

    async def get_traffic(self, email: str) -> Optional[int]:
        """Получает трафик клиента"""
        t0 = time.monotonic()
        xui_api_calls_total.labels(method="get_traffic").inc()
        try:
            await self.ensure_auth()
            client = await self.xui.client.get_by_email(email)
            return client.total_gb if client else None

        except Exception as e:
            xui_api_errors_total.labels(
                method="get_traffic", error_type=type(e).__name__
            ).inc()
            logger.error(
                "Ошибка при получении трафика",
                email=email,
                error_type=type(e).__name__,
                error_message=str(e),
                exc_info=True,
            )
            return None
        finally:
            xui_api_duration.labels(method="get_traffic").observe(
                time.monotonic() - t0
            )

    async def delete_old_client(self, inbound: Inbound):
        """Удаляет старые ключи"""
        await self.ensure_auth()
        old_time = get_timestamp_30_days_ago()
        inbound_id = inbound.id
        data_client = inbound.settings.clients
        old_clients = [
            client
            for client in data_client
            if client.expiry_time <= old_time and client.expiry_time > 0
        ]
        logger.debug(
            "Истекшие ключи для подключения получены",
            count_client=len(old_clients),
            inbound_id=inbound_id,
        )
        task_list = []
        for client in old_clients:
            task = self.delete_client(
                email=client.email, inbound_id=inbound_id, client_id=client.id
            )
            task_list.append(task)
        deletion_results = await asyncio.gather(*task_list)
        successful_deletions = sum(1 for result in deletion_results if result)
        logger.debug("Ключи удалены", inbound_id=inbound_id, count=successful_deletions)

    async def close(self):
        """Закрывает сессию"""
        self._is_authenticated = False
        # XUI API обычно не требует явного закрытия


def get_timestamp_30_days_ago():
    """Возвращает timestamp в миллисекундах для даты 30 дней назад"""
    # Текущее время
    now = datetime.now()
    # Время 30 дней назад
    days_ago = now - timedelta(days=30)
    # Конвертируем в timestamp в миллисекундах
    timestamp_ms = int(days_ago.timestamp() * 1000)
    return timestamp_ms
