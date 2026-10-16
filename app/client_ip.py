from functools import lru_cache
from ipaddress import ip_address, ip_network, IPv4Network, IPv6Network

from fastapi import Request

from config import settings


@lru_cache(maxsize=1)
def _trusted_networks() -> tuple[IPv4Network | IPv6Network, ...]:
    return tuple(ip_network(cidr, strict=False) for cidr in settings.trusted_proxy_cidrs)


def _is_trusted(ip: str) -> bool:
    try:
        addr = ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in _trusted_networks())


def extract_client_ip(request: Request) -> str | None:
    """Извлекает реальный IP клиента с учётом прокси-заголовков.

    Общий хелпер: используется и для проверки IP webhook YooKassa
    (``api/v1/payments.py``), и для rate limiting (``app/rate_limit.py``).

    Заголовкам верим только от своих прокси (``TRUSTED_PROXY_CIDRS``, по
    умолчанию loopback и частные сети docker). Цепочку X-Forwarded-For идём
    справа налево и берём первый адрес, который не является нашим прокси, —
    как ``real_ip_recursive`` в nginx. Левые элементы цепочки клиент может
    подставить сам (``proxy_add_x_forwarded_for`` дописывает, а не затирает),
    поэтому первый элемент брать нельзя: так обходились rate limit и
    IP-фильтр webhook.
    """
    peer = request.client.host if request.client else None
    if peer is None or not _is_trusted(peer):
        return peer

    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        chain = [part.strip() for part in forwarded_for.split(",") if part.strip()]
        for candidate in reversed(chain):
            if not _is_trusted(candidate):
                return candidate
        if chain:
            return chain[0]

    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()

    return peer
