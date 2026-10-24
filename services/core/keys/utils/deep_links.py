"""Deep-link ссылки для импорта ключа в VPN-клиенты.

Одно место, которое знает формат ссылки клиента: лендинг и кабинет (Mini App, веб)
отдают одну и ту же ссылку, клиенты её не собирают.
"""

from typing import Optional

HAPP_ADD_PREFIX = "happ://add/"


def happ_add_link(subscription_url: Optional[str]) -> Optional[str]:
    """Ссылка «открыть в Happ» для subscription URL ключа.

    Happ принимает URL **без** URL-кодирования — см. апстрим 3x-ui
    (PR MHSanaei/3x-ui#3863, «Fix DeepLink for Happ, remove encoding URL»).
    Любое кодирование или иные действия (happ://import/, извлечение vless://)
    Happ отвергает ошибкой «Неизвестное действие DeepLink».
    """
    if not subscription_url:
        return None
    return f"{HAPP_ADD_PREFIX}{subscription_url}"
