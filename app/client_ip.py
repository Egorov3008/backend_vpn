from fastapi import Request


def extract_client_ip(request: Request) -> str | None:
    """Извлекает реальный IP клиента с учётом прокси-заголовков.

    Общий хелпер: используется и для проверки IP webhook YooKassa
    (``api/v1/payments.py``), и для rate limiting (``app/rate_limit.py``).
    """
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        client_ip_str = forwarded_for.split(",")[0].strip()
        if client_ip_str:
            return client_ip_str

    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip

    return request.client.host if request.client else None
