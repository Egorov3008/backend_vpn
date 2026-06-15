from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timedelta
from multiprocessing import AuthenticationError
from typing import Optional, Any

import httpx
from circuit_breaker import CircuitBreaker, CircuitBreakerError
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
from config import settings

# Circuit breaker for XUI API calls
# Fails after 5 consecutive failures, resets after 30 seconds
_xui_circuit_breaker = CircuitBreaker(
    fail_max=5,
    reset_timeout=30,
    success_threshold=1,
    name="xui_api",
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


class XUIAuthError(Exception):
    """Ошибка аутентификации в 3x-ui панели (standalone API)."""


from dataclasses import dataclass

@dataclass
class PanelClient:
    """DTO клиента из 3x-ui панели (v3.2.0 standalone API).

    Заменяет py3xui.Client во всех сервисах синхронизации.
    """
    id: str = ""           # UUID (VMess/VLESS)
    email: str = ""
    tg_id: int = 0
    limit_ip: int = 0
    total_gb: int = 0
    expiry_time: int = 0
    inbound_id: int = 0    # Первый inbound из inboundIds (legacy compat)
    inbound_ids: list = None  # Все inbound IDs (v3.2.0)
    sub_id: str = ""
    enable: bool = True
    flow: str = ""
    group: str = ""
    comment: str = ""


class _StandaloneClientAPI:
    """Native httpx client for 3x-ui v3.2.0 standalone Clients API.

    Supports two auth modes:
      1. Bearer token (API Token from panel settings) — preferred, no CSRF.
      2. Session cookie (reused from py3xui or obtained via CSRF+login).
    """

    def __init__(
        self,
        base_url: str,
        username: str = "",
        password: str = "",
        session_cookie: Optional[str] = None,
        token: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        if not self.base_url.endswith("/panel"):
            self.base_url += "/panel"
        self.username = username
        self.password = password
        self._token = token
        self._session_cookie = session_cookie
        self._client = httpx.AsyncClient(timeout=30.0)

    def set_cookie(self, cookie: str) -> None:
        self._session_cookie = cookie

    async def _ensure_auth(self) -> None:
        """No-op when using Bearer token; otherwise obtains CSRF+session."""
        if self._token or self._session_cookie:
            return

        # 1. Получаем CSRF token + начальный session cookie
        csrf_resp = await self._client.get(
            f"{self.base_url}/csrf-token",
            headers={"Accept": "application/json"},
        )
        csrf_resp.raise_for_status()
        csrf_data = csrf_resp.json()
        csrf_token = csrf_data.get("obj") or csrf_data.get("csrfToken")
        if not csrf_token:
            raise XUIAuthError(f"Не удалось получить CSRF token: {csrf_data}")

        # Пытаемся вытащить session cookie из ответа CSRF
        for cookie_name in ("session", "3x-ui"):
            cookie = csrf_resp.cookies.get(cookie_name)
            if cookie:
                self._session_cookie = cookie
                break

        # 2. Логинимся с CSRF token
        login_resp = await self._client.post(
            f"{self.base_url}/login",
            data={"username": self.username, "password": self.password},
            headers={"X-CSRF-Token": csrf_token},
        )
        login_resp.raise_for_status()

        # 3. Вытаскиваем итоговый session cookie
        for cookie_name in ("session", "3x-ui"):
            cookie = login_resp.cookies.get(cookie_name)
            if cookie:
                self._session_cookie = cookie
                break

        if not self._session_cookie:
            raise XUIAuthError(
                "Не удалось получить session cookie от панели после login"
            )

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        await self._ensure_auth()
        url = f"{self.base_url}{path}"
        headers = kwargs.pop("headers", {})
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        elif self._session_cookie:
            headers["Cookie"] = f"session={self._session_cookie}"
        return await self._client.request(method, url, headers=headers, **kwargs)

    # ── Standalone Clients API (v3.2.0) ──

    async def add(self, client_data: dict, inbound_ids: list[int]) -> dict:
        """POST /api/clients/add"""
        resp = await self._request(
            "POST",
            "/api/clients/add",
            json={"client": client_data, "inboundIds": inbound_ids},
        )
        resp.raise_for_status()
        return resp.json()

    async def attach(self, email: str, inbound_ids: list[int]) -> dict:
        """POST /api/clients/{email}/attach"""
        resp = await self._request(
            "POST",
            f"/api/clients/{email}/attach",
            json={"inboundIds": inbound_ids},
        )
        resp.raise_for_status()
        return resp.json()

    async def detach(self, email: str, inbound_ids: list[int]) -> dict:
        """POST /api/clients/{email}/detach"""
        resp = await self._request(
            "POST",
            f"/api/clients/{email}/detach",
            json={"inboundIds": inbound_ids},
        )
        resp.raise_for_status()
        return resp.json()

    async def delete(self, email: str, keep_traffic: bool = False) -> dict:
        """POST /api/clients/del/{email}"""
        params = {"keepTraffic": "1"} if keep_traffic else None
        resp = await self._request(
            "POST", f"/api/clients/del/{email}", params=params
        )
        resp.raise_for_status()
        return resp.json()

    async def get(self, email: str) -> dict:
        """GET /api/clients/get/{email}"""
        resp = await self._request("GET", f"/api/clients/get/{email}")
        resp.raise_for_status()
        return resp.json()

    async def update(self, email: str, fields: dict) -> dict:
        """POST /api/clients/update/{email}"""
        payload = dict(fields)
        payload.setdefault("email", email)
        resp = await self._request(
            "POST", f"/api/clients/update/{email}", json=payload
        )
        resp.raise_for_status()
        return resp.json()

    async def reset_traffic(self, email: str) -> dict:
        """POST /api/clients/resetTraffic/{email}"""
        resp = await self._request(
            "POST", f"/api/clients/resetTraffic/{email}"
        )
        resp.raise_for_status()
        return resp.json()

    async def onlines(self) -> dict:
        """POST /api/clients/onlines"""
        resp = await self._request("POST", "/api/clients/onlines")
        resp.raise_for_status()
        return resp.json()

    async def list(self) -> dict:
        """GET /api/clients/list"""
        resp = await self._request("GET", "/api/clients/list")
        resp.raise_for_status()
        return resp.json()

    async def list_inbounds(self) -> dict:
        """GET /api/inbounds/list"""
        resp = await self._request("GET", "/api/inbounds/list")
        resp.raise_for_status()
        return resp.json()

    async def bulk_attach(self, emails: list[str], inbound_ids: list[int]) -> dict:
        """POST /api/clients/bulkAttach"""
        resp = await self._request(
            "POST",
            "/api/clients/bulkAttach",
            json={"emails": emails, "inboundIds": inbound_ids},
        )
        resp.raise_for_status()
        return resp.json()

    async def bulk_detach(self, emails: list[str], inbound_ids: list[int]) -> dict:
        """POST /api/clients/bulkDetach"""
        resp = await self._request(
            "POST",
            "/api/clients/bulkDetach",
            json={"emails": emails, "inboundIds": inbound_ids},
        )
        resp.raise_for_status()
        return resp.json()


class XUISession:
    """Единая сессия для работы с XUI панелью"""

    def __init__(
        self, model_service: ServiceDataModel, loading: LoadingService,
        login_timeout: float = 15.0, login_max_retries: int = 1
    ) -> None:
        self.server_id = settings.xui_server_id
        self._is_authenticated = False
        self.server = None
        self._standalone: Optional[_StandaloneClientAPI] = None
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
            # Fallback: build Server from .env when DB servers table is empty
            from models.servers.server import get_env_server
            self.server = get_env_server()
            logger.debug(
                "Сервер загружен из .env", extra={"server": self.server, "server_id": self.server_id}
            )

        # Ленивая инициализация standalone API клиента при первом вызове
        # через _ensure_standalone()

    async def ensure_auth(self):
        """Аутентификация через standalone API (Bearer token или CSRF+login)."""
        await self._ensure_initialized()
        if not self._is_authenticated:
            await self._ensure_standalone()
            t0 = time.monotonic()
            xui_login_calls_total.inc()
            try:
                async def _auth():
                    await asyncio.wait_for(
                        self._standalone._ensure_auth(),
                        timeout=max(self.login_timeout, 35.0)
                    )
                await _xui_circuit_breaker.call_async(_auth)
                login_duration = time.monotonic() - t0
                self._is_authenticated = True
                xui_login_duration.observe(login_duration)
                if login_duration > 5.0:
                    logger.warning(
                        "Login выполнен медленно",
                        extra={"duration_sec": round(login_duration, 2), "timeout": self.login_timeout}
                    )
                else:
                    logger.debug(
                        "Login выполнен успешно",
                        extra={"duration_sec": round(login_duration, 3)}
                    )
            except CircuitBreakerError:
                xui_api_errors_total.labels(method="login", error_type="CircuitBreakerError").inc()
                logger.error("XUI circuit breaker open - skipping login")
                raise
            except asyncio.TimeoutError:
                xui_api_errors_total.labels(method="login", error_type="TimeoutError").inc()
                logger.error(
                    "Превышен таймаут login",
                    extra={"timeout": self.login_timeout, "host": self.server.api_url}
                )
                raise TimeoutError(
                    f"Login превысил таймаут {self.login_timeout}сек"
                )
            except Exception as e:
                xui_api_errors_total.labels(method="login", error_type=type(e).__name__).inc()
                # exc_info=True (keyword arg) forces the logger to include the
                # full traceback in errors.log. Passing it via ``extra`` is a
                # silent no-op — that's why we've been debugging blind.
                logger.error(
                    "Ошибка при login",
                    extra={"error_type": type(e).__name__, "error_message": str(e)},
                    exc_info=True,
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
        inbound_ids: list,
        expiry_time: int,
        total_gb: int,
        enable: bool = True,
        flow: str = "xtls-rprx-vision",
    ) -> Any:
        """Добавляет standalone-клиента и привязывает к inbound-ам (v3.2.0 API)."""
        await self.ensure_auth()
        await self._ensure_standalone()
        t0 = time.monotonic()
        xui_api_calls_total.labels(method="add_client").inc()
        try:
            client_data = {
                "id": client_id,
                "email": email,
                "limitIp": limit_ip,
                "totalGB": total_gb,
                "expiryTime": expiry_time,
                "enable": enable,
                "tgId": tg_id,
                "flow": flow,
                "subId": email,
                "group": "",
                "comment": "",
                "reset": 0,
            }
            async def _add():
                return await self._standalone.add(client_data, inbound_ids)
            await _xui_circuit_breaker.call_async(_add)
            logger.info(
                "Клиент успешно добавлен", extra={"email": email, "client_id": client_id}
            )
            return True

        except CircuitBreakerError:
            xui_api_errors_total.labels(method="add_client", error_type="CircuitBreakerError").inc()
            logger.error("XUI circuit breaker open - add_client skipped", extra={"email": email})
            return False
        except Exception as e:
            xui_api_errors_total.labels(
                method="add_client", error_type=type(e).__name__
            ).inc()
            logger.error(
                "Ошибка при добавлении клиента",
                extra={"email": email, "error_type": type(e).__name__, "error_message": str(e), "exc_info": True}
            )
            return False
        finally:
            xui_api_duration.labels(method="add_client").observe(
                time.monotonic() - t0
            )

    async def _get_client_or_none(self, email: str) -> Optional[dict]:
        """Получает standalone-клиента по email; возвращает None если не найден."""
        try:
            await self.ensure_auth()
            await self._ensure_standalone()
            return await self._standalone.get(email)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
        except Exception as e:
            if "not found" in str(e).lower():
                return None
            raise

    async def extend_client_key(
        self,
        key_details: Key,
    ) -> bool:
        """Обновляет срок действия ключа клиента через standalone API (v3.2.0)."""
        t0 = time.monotonic()
        xui_api_calls_total.labels(method="extend_client_key").inc()
        try:
            await self.ensure_auth()
            await self._ensure_standalone()

            # Проверяем, что клиент существует в панели
            try:
                await self._standalone.get(key_details.email)
            except Exception:
                logger.warning("Клиент не найден", extra={"email": key_details.email})
                return False

            # Расчет нового времени expiry
            expiry_datetime = datetime.fromtimestamp(key_details.expiry_time / 1000)
            logger.debug(
                "Проверка присваемого времени",
                extra={"expiry_time": key_details.expiry_time, "expiry_datetime": expiry_datetime}
            )

            await self._standalone.update(key_details.email, {
                "totalGB": key_details.total_gb,
                "expiryTime": key_details.expiry_time,
                "limitIp": int(key_details.limit_ip) if key_details.limit_ip is not None else 1,
                "flow": "xtls-rprx-vision",
                "subId": key_details.email,
                "enable": True,
            })
            # reset_traffic НЕ вызываем — 3x-ui обнуляет totalGB и expiryTime (баг #6cx7ah)
            # Сброс трафика происходит через БД (used_traffic=0) после продления

            logger.info("Ключ клиента продлён", extra={"email": key_details.email})
            return True

        except Exception as e:
            xui_api_errors_total.labels(
                method="extend_client_key", error_type=type(e).__name__
            ).inc()
            logger.error(
                "Ошибка при продлении ключа ",
                extra={"email": key_details.email, "error_type": type(e).__name__, "error_message": str(e), "exc_info": True}
            )
            return False
        finally:
            xui_api_duration.labels(method="extend_client_key").observe(
                time.monotonic() - t0
            )

    @filter_by_method_signature
    async def delete_client(self, email: str, inbound_id: int, client_id: str) -> bool:
        """Удаляет standalone-клиента с сервера по email (v3.2.0 API).

        Аргументы inbound_id и client_id сохранены для backward compatibility,
        но больше не используются — standalone API удаляет по email.
        """
        t0 = time.monotonic()
        xui_api_calls_total.labels(method="delete_client").inc()
        try:
            await self.ensure_auth()
            await self._ensure_standalone()

            # Проверяем существование клиента
            try:
                await self._standalone.get(email)
            except Exception:
                logger.warning("Клиент не найден в панели, считаем удалённым", extra={"email": email})
                return True

            await self._standalone.delete(email)
            logger.info("Клиент удалён", extra={"email": email})
            return True

        except Exception as e:
            err_msg = str(e).lower()
            if "not found" in err_msg:
                logger.warning("Клиент не найден в панели, считаем удалённым", extra={"email": email})
                return True
            xui_api_errors_total.labels(
                method="delete_client", error_type=type(e).__name__
            ).inc()
            logger.error(
                "Ошибка при удалении клиента",
                extra={"email": email, "error_type": type(e).__name__, "error_message": str(e), "exc_info": True}
            )
            return False
        finally:
            xui_api_duration.labels(method="delete_client").observe(
                time.monotonic() - t0
            )

    async def get_inbounds(self) -> list[dict]:
        """Получает список инбаундов через standalone API (v3.2.0)."""
        t0 = time.monotonic()
        xui_api_calls_total.labels(method="get_inbounds").inc()
        try:
            await self.ensure_auth()
            await self._ensure_standalone()
            result = await self._standalone.list_inbounds()
            inbounds = result.get("obj", []) if isinstance(result, dict) else result
            return list(inbounds) if inbounds else []

        except Exception as e:
            xui_api_errors_total.labels(
                method="get_inbounds", error_type=type(e).__name__
            ).inc()
            logger.error(
                "Ошибка при получении списка подключений",
                extra={"error_type": type(e).__name__, "error_message": str(e)}
            )
            return []
        finally:
            xui_api_duration.labels(method="get_inbounds").observe(
                time.monotonic() - t0
            )

    async def get_traffic(self, email: str) -> Optional[int]:
        """Получает трафик клиента через standalone API."""
        t0 = time.monotonic()
        xui_api_calls_total.labels(method="get_traffic").inc()
        try:
            await self.ensure_auth()
            client = await self._get_client_or_none(email)
            if not client:
                return None
            # standalone API возвращает dict с camelCase или snake_case
            return client.get("totalGB") or client.get("total_gb") or 0

        except Exception as e:
            xui_api_errors_total.labels(
                method="get_traffic", error_type=type(e).__name__
            ).inc()
            logger.error(
                "Ошибка при получении трафика",
                extra={"email": email, "error_type": type(e).__name__, "error_message": str(e), "exc_info": True}
            )
            return None
        finally:
            xui_api_duration.labels(method="get_traffic").observe(
                time.monotonic() - t0
            )

    async def delete_old_clients(self) -> dict:
        """Удаляет standalone-клиентов с истекшим сроком (v3.2.0 API)."""
        await self.ensure_auth()
        await self._ensure_standalone()
        old_time = get_timestamp_30_days_ago()
        try:
            raw_list = await self._standalone.list()
            clients = raw_list.get("obj", []) if isinstance(raw_list, dict) else raw_list
        except Exception as e:
            logger.error("Ошибка получения списка клиентов для удаления", error=str(e))
            return {"deleted": 0, "failed": 0}

        old_clients = [
            c for c in clients
            if isinstance(c, dict)
            and c.get("expiryTime", 0) <= old_time
            and c.get("expiryTime", 0) > 0
        ]
        logger.debug(
            "Истекшие standalone клиенты получены",
            extra={"count_client": len(old_clients)}
        )
        task_list = []
        for client in old_clients:
            task = self.delete_client(
                email=client.get("email", ""), inbound_id=0, client_id=client.get("id", "")
            )
            task_list.append(task)
        deletion_results = await asyncio.gather(*task_list)
        successful_deletions = sum(1 for result in deletion_results if result)
        logger.debug("Ключи удалены", extra={"count": successful_deletions})
        return {"deleted": successful_deletions, "failed": len(old_clients) - successful_deletions}

    # ── Standalone Clients API helpers (v3.2.0) ──

    async def _ensure_standalone(self) -> None:
        """Ленивая инициализация standalone API клиента."""
        import os

        if self._standalone is None:
            token = os.environ.get("XUI_TOKEN") or os.environ.get("XUI_API_TOKEN")
            self._standalone = _StandaloneClientAPI(
                base_url=self.server.api_url,
                username=self.server.login,
                password=self.server.password,
                token=token,
            )

    @retry(
        stop=stop.stop_after_attempt(3),
        wait=wait.wait_fixed(2),
        retry=retry_if_exception(XUIRetryPolicy.is_retryable_exception),
        reraise=True,
    )
    async def attach_to_inbounds(self, email: str, inbound_ids: list[int]) -> dict:
        """Привязывает standalone-клиента к указанным inbound-ам (v3.2.0 API)."""
        await self.ensure_auth()
        await self._ensure_standalone()
        t0 = time.monotonic()
        xui_api_calls_total.labels(method="attach_to_inbounds").inc()
        try:
            result = await self._standalone.attach(email, inbound_ids)
            logger.info(
                "Клиент привязан к inbounds",
                extra={"email": email, "inbound_ids": inbound_ids}
            )
            return result
        except Exception as e:
            xui_api_errors_total.labels(
                method="attach_to_inbounds", error_type=type(e).__name__
            ).inc()
            logger.error(
                "Ошибка при привязке клиента к inbounds",
                extra={"email": email, "error_type": type(e).__name__, "error_message": str(e), "exc_info": True}
            )
            raise
        finally:
            xui_api_duration.labels(method="attach_to_inbounds").observe(
                time.monotonic() - t0
            )

    @retry(
        stop=stop.stop_after_attempt(3),
        wait=wait.wait_fixed(2),
        retry=retry_if_exception(XUIRetryPolicy.is_retryable_exception),
        reraise=True,
    )
    async def detach_from_inbounds(self, email: str, inbound_ids: list[int]) -> dict:
        """Отвязывает standalone-клиента от указанных inbound-ов (v3.2.0 API)."""
        await self.ensure_auth()
        await self._ensure_standalone()
        t0 = time.monotonic()
        xui_api_calls_total.labels(method="detach_from_inbounds").inc()
        try:
            result = await self._standalone.detach(email, inbound_ids)
            logger.info(
                "Клиент отвязан от inbounds",
                extra={"email": email, "inbound_ids": inbound_ids}
            )
            return result
        except Exception as e:
            xui_api_errors_total.labels(
                method="detach_from_inbounds", error_type=type(e).__name__
            ).inc()
            logger.error(
                "Ошибка при отвязке клиента от inbounds",
                extra={"email": email, "error_type": type(e).__name__, "error_message": str(e), "exc_info": True}
            )
            raise
        finally:
            xui_api_duration.labels(method="detach_from_inbounds").observe(
                time.monotonic() - t0
            )

    @retry(
        stop=stop.stop_after_attempt(3),
        wait=wait.wait_fixed(2),
        retry=retry_if_exception(XUIRetryPolicy.is_retryable_exception),
        reraise=True,
    )
    async def add_standalone_client(
        self,
        email: str,
        client_id: str,
        inbound_ids: list[int],
        tg_id: int = 0,
        limit_ip: int = 2,
        total_gb: int = 0,
        expiry_time: int = 0,
        enable: bool = True,
        flow: str = "xtls-rprx-vision",
        sub_id: str = "",
        group: str = "",
        comment: str = "",
        reset: int = 0,
    ) -> dict:
        """Создаёт standalone-клиента и сразу привязывает к inbound-ам (v3.2.0 API)."""
        await self.ensure_auth()
        await self._ensure_standalone()
        t0 = time.monotonic()
        xui_api_calls_total.labels(method="add_standalone_client").inc()
        try:
            client_data = {
                "id": client_id,
                "email": email,
                "limitIp": limit_ip,
                "totalGB": total_gb,
                "expiryTime": expiry_time,
                "enable": enable,
                "tgId": tg_id,
                "flow": flow,
                "subId": sub_id or email,
                "group": group,
                "comment": comment,
                "reset": reset,
            }
            result = await self._standalone.add(client_data, inbound_ids)
            logger.info(
                "Standalone клиент создан",
                extra={"email": email, "client_id": client_id, "inbound_ids": inbound_ids}
            )
            return result
        except Exception as e:
            xui_api_errors_total.labels(
                method="add_standalone_client", error_type=type(e).__name__
            ).inc()
            logger.error(
                "Ошибка при создании standalone клиента",
                extra={"email": email, "error_type": type(e).__name__, "error_message": str(e), "exc_info": True}
            )
            raise
        finally:
            xui_api_duration.labels(method="add_standalone_client").observe(
                time.monotonic() - t0
            )

    @retry(
        stop=stop.stop_after_attempt(3),
        wait=wait.wait_fixed(2),
        retry=retry_if_exception(XUIRetryPolicy.is_retryable_exception),
        reraise=True,
    )
    async def delete_standalone_client(self, email: str, keep_traffic: bool = False) -> dict:
        """Удаляет standalone-клиента по email (v3.2.0 API)."""
        await self.ensure_auth()
        await self._ensure_standalone()
        t0 = time.monotonic()
        xui_api_calls_total.labels(method="delete_standalone_client").inc()
        try:
            result = await self._standalone.delete(email, keep_traffic=keep_traffic)
            logger.info(
                "Standalone клиент удалён",
                extra={"email": email, "keep_traffic": keep_traffic}
            )
            return result
        except Exception as e:
            xui_api_errors_total.labels(
                method="delete_standalone_client", error_type=type(e).__name__
            ).inc()
            logger.error(
                "Ошибка при удалении standalone клиента",
                extra={"email": email, "error_type": type(e).__name__, "error_message": str(e), "exc_info": True}
            )
            raise
        finally:
            xui_api_duration.labels(method="delete_standalone_client").observe(
                time.monotonic() - t0
            )

    @retry(
        stop=stop.stop_after_attempt(3),
        wait=wait.wait_fixed(2),
        retry=retry_if_exception(XUIRetryPolicy.is_retryable_exception),
        reraise=True,
    )
    async def get_standalone_client(self, email: str) -> dict:
        """Получает standalone-клиента по email (v3.2.0 API)."""
        await self.ensure_auth()
        await self._ensure_standalone()
        t0 = time.monotonic()
        xui_api_calls_total.labels(method="get_standalone_client").inc()
        try:
            result = await self._standalone.get(email)
            return result
        except Exception as e:
            xui_api_errors_total.labels(
                method="get_standalone_client", error_type=type(e).__name__
            ).inc()
            logger.error(
                "Ошибка при получении standalone клиента",
                extra={"email": email, "error_type": type(e).__name__, "error_message": str(e), "exc_info": True}
            )
            raise
        finally:
            xui_api_duration.labels(method="get_standalone_client").observe(
                time.monotonic() - t0
            )

    @retry(
        stop=stop.stop_after_attempt(3),
        wait=wait.wait_fixed(2),
        retry=retry_if_exception(XUIRetryPolicy.is_retryable_exception),
        reraise=True,
    )
    async def list_clients(self) -> list[dict]:
        """Получает список всех standalone-клиентов (v3.2.0 API)."""
        await self.ensure_auth()
        await self._ensure_standalone()
        t0 = time.monotonic()
        xui_api_calls_total.labels(method="list_clients").inc()
        try:
            result = await self._standalone.list()
            return result.get("obj", []) if isinstance(result, dict) else result
        except Exception as e:
            xui_api_errors_total.labels(
                method="list_clients", error_type=type(e).__name__
            ).inc()
            logger.error(
                "Ошибка при получении списка standalone клиентов",
                extra={"error_type": type(e).__name__, "error_message": str(e), "exc_info": True}
            )
            raise
        finally:
            xui_api_duration.labels(method="list_clients").observe(
                time.monotonic() - t0
            )

    @retry(
        stop=stop.stop_after_attempt(3),
        wait=wait.wait_fixed(2),
        retry=retry_if_exception(XUIRetryPolicy.is_retryable_exception),
        reraise=True,
    )
    async def update_standalone_client(self, email: str, **fields) -> dict:
        """Обновляет поля standalone-клиента по email (v3.2.0 API)."""
        await self.ensure_auth()
        await self._ensure_standalone()
        t0 = time.monotonic()
        xui_api_calls_total.labels(method="update_standalone_client").inc()
        try:
            result = await self._standalone.update(email, fields)
            logger.info(
                "Standalone клиент обновлён",
                extra={"email": email, "fields": list(fields.keys())}
            )
            return result
        except Exception as e:
            xui_api_errors_total.labels(
                method="update_standalone_client", error_type=type(e).__name__
            ).inc()
            logger.error(
                "Ошибка при обновлении standalone клиента",
                extra={"email": email, "error_type": type(e).__name__, "error_message": str(e), "exc_info": True}
            )
            raise
        finally:
            xui_api_duration.labels(method="update_standalone_client").observe(
                time.monotonic() - t0
            )

    @retry(
        stop=stop.stop_after_attempt(3),
        wait=wait.wait_fixed(2),
        retry=retry_if_exception(XUIRetryPolicy.is_retryable_exception),
        reraise=True,
    )
    async def reset_standalone_traffic(self, email: str) -> dict:
        """Сбрасывает трафик standalone-клиента (v3.2.0 API)."""
        await self.ensure_auth()
        await self._ensure_standalone()
        t0 = time.monotonic()
        xui_api_calls_total.labels(method="reset_standalone_traffic").inc()
        try:
            result = await self._standalone.reset_traffic(email)
            logger.info(
                "Трафик standalone клиента сброшен",
                extra={"email": email}
            )
            return result
        except Exception as e:
            xui_api_errors_total.labels(
                method="reset_standalone_traffic", error_type=type(e).__name__
            ).inc()
            logger.error(
                "Ошибка при сбросе трафика standalone клиента",
                extra={"email": email, "error_type": type(e).__name__, "error_message": str(e), "exc_info": True}
            )
            raise
        finally:
            xui_api_duration.labels(method="reset_standalone_traffic").observe(
                time.monotonic() - t0
            )

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
