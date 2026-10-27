"""Bot API пользовательского бота для рассылок (httpx, без aiogram).

Токен живёт только в URL запросов и никуда не пишется: в ошибки и логи
попадают описание от Telegram или имя исключения, но не адрес.
"""
import asyncio
from typing import Any, BinaryIO, Optional, Protocol

import httpx

from logger import logger
from services.broadcasts.models import BroadcastMessage, DeliveryStatus, Media, MediaType, SendResult

_METHOD_BY_MEDIA = {
    MediaType.PHOTO: "sendPhoto",
    MediaType.VIDEO: "sendVideo",
    MediaType.ANIMATION: "sendAnimation",
    MediaType.AUDIO: "sendAudio",
    MediaType.DOCUMENT: "sendDocument",
}
# Куда Telegram кладёт файл в ответе. Он может сменить тип (видео не в MP4 придёт
# как document, GIF — как animation), поэтому после запрошенного смотрим остальные.
_RESULT_ORDER = (MediaType.ANIMATION, MediaType.VIDEO, MediaType.AUDIO, MediaType.PHOTO, MediaType.DOCUMENT)
# Пауза при сетевом сбое: отправку повторит раннер, как после 429.
_NETWORK_RETRY_AFTER = 2.0
# Попыток дослать текст с кнопкой после альбома (при 429 и сбоях сети)
_FOLLOW_UP_ATTEMPTS = 3


class BroadcastBot(Protocol):
    async def send(self, chat_id: int, message: BroadcastMessage) -> SendResult: ...

    async def upload(
        self, chat_id: int, media_type: MediaType, file_name: str, content_type: str, file: BinaryIO
    ) -> tuple[SendResult, Optional[Media]]: ...

    async def delete_message(self, chat_id: int, message_id: int) -> None: ...

    async def pin(self, chat_id: int, message_id: int) -> None: ...


def reply_markup(message: BroadcastMessage) -> Optional[dict]:
    button = message.button
    if button is None:
        return None
    target = {"web_app": {"url": button.url}} if button.kind == "miniapp" else {"url": button.url}
    return {"inline_keyboard": [[{"text": button.text, **target}]]}


def _album_payload(chat_id: int, message: BroadcastMessage) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for i, media in enumerate(message.media):
        item: dict[str, Any] = {"type": media.type.value, "media": media.file_id}
        # Подпись альбома — подпись первого файла (так её показывает Telegram)
        if i == 0 and message.text and not message.text_with_button:
            item.update(caption=message.text, parse_mode="HTML")
        if media.type == MediaType.VIDEO:
            item["supports_streaming"] = True
        items.append(item)
    return {"chat_id": chat_id, "media": items, "disable_notification": message.disable_notification}


def build_requests(chat_id: int, message: BroadcastMessage) -> list[tuple[str, dict[str, Any]]]:
    """Запросы Bot API для одного получателя, по порядку.

    Обычно один. Альбом с кнопкой — два: sendMediaGroup (кнопку к альбому
    прикрепить нельзя) и следом текст с кнопкой."""
    base: dict[str, Any] = {"chat_id": chat_id, "disable_notification": message.disable_notification}
    markup = reply_markup(message)
    text_message = {**base, "text": message.text, "parse_mode": "HTML"}
    if markup is not None:
        text_message["reply_markup"] = markup
    if not message.media:
        return [("sendMessage", text_message)]
    if message.is_album:
        requests = [("sendMediaGroup", _album_payload(chat_id, message))]
        if message.text_with_button:
            requests.append(("sendMessage", text_message))
        return requests
    media = message.media[0]
    payload = {**base, media.type.value: media.file_id}
    if markup is not None:
        payload["reply_markup"] = markup
    if message.text:
        payload.update(caption=message.text, parse_mode="HTML")
    if media.type == MediaType.VIDEO:
        payload["supports_streaming"] = True
    return [(_METHOD_BY_MEDIA[media.type], payload)]


def parse_response(status_code: int, body: Any) -> SendResult:
    """Ответ Bot API → итог отправки одному человеку."""
    if not isinstance(body, dict):
        return SendResult(DeliveryStatus.FAILED, error=f"HTTP {status_code}")
    if body.get("ok"):
        result = body.get("result")
        if isinstance(result, list):  # альбом: закрепляем первое сообщение
            result = result[0] if result else None
        message_id = result.get("message_id") if isinstance(result, dict) else None
        return SendResult(DeliveryStatus.SENT, message_id=message_id)
    description = str(body.get("description") or f"HTTP {status_code}")[:500]
    code = body.get("error_code") or status_code
    if code == 429:
        retry_after = (body.get("parameters") or {}).get("retry_after") or 1
        return SendResult(DeliveryStatus.FAILED, error=description, retry_after=float(retry_after))
    if code == 401:
        return SendResult(DeliveryStatus.FAILED, error=description, unauthorized=True)
    if code == 403 or "chat not found" in description.lower():
        return SendResult(DeliveryStatus.BLOCKED, error=description)
    return SendResult(DeliveryStatus.FAILED, error=description)


def extract_media(result: Any, requested: MediaType, file_name: Optional[str]) -> Optional[Media]:
    """file_id загруженного файла из сообщения, которое вернул Telegram."""
    if not isinstance(result, dict):
        return None
    for media_type in (requested, *[t for t in _RESULT_ORDER if t != requested]):
        found = result.get(media_type.value)
        if media_type == MediaType.PHOTO and isinstance(found, list) and found:
            found = found[-1]  # самый крупный размер
        if isinstance(found, dict) and found.get("file_id"):
            return Media(type=media_type, file_id=found["file_id"], file_name=file_name)
    return None


class TelegramBroadcastBot:
    def __init__(self, bot_token: str, timeout: float = 15.0, upload_timeout: float = 300.0):
        self._token = bot_token
        self._timeout = timeout
        self._upload_timeout = upload_timeout

    @property
    def configured(self) -> bool:
        return bool(self._token)

    def _url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self._token}/{method}"

    async def _call(self, method: str, timeout: float, **kwargs) -> tuple[SendResult, Any]:
        if not self._token:
            return SendResult(DeliveryStatus.FAILED, error="BOT_TOKEN не задан", unauthorized=True), None
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(self._url(method), **kwargs)
            try:
                body = resp.json()
            except ValueError:
                body = None
        except httpx.HTTPError as e:
            # str(e) у некоторых исключений httpx содержит URL, а в нём токен
            return SendResult(DeliveryStatus.FAILED, error=f"Сеть: {type(e).__name__}",
                              retry_after=_NETWORK_RETRY_AFTER, network_error=True), None
        result = parse_response(resp.status_code, body)
        return result, (body or {}).get("result") if result.ok else None

    async def send(self, chat_id: int, message: BroadcastMessage) -> SendResult:
        (method, payload), *rest = build_requests(chat_id, message)
        result, _ = await self._call(method, self._timeout, json=payload)
        if not result.ok:
            return result
        # Альбом уже у человека: следующее сообщение (текст с кнопкой) досылаем здесь же,
        # иначе повтор всей отправки продублировал бы альбом.
        for follow_method, follow_payload in rest:
            await self._send_follow_up(chat_id, follow_method, follow_payload)
        return result

    async def _send_follow_up(self, chat_id: int, method: str, payload: dict[str, Any]) -> None:
        for _ in range(_FOLLOW_UP_ATTEMPTS):
            follow, _ = await self._call(method, self._timeout, json=payload)
            if follow.ok:
                return
            if follow.retry_after is None:
                break
            await asyncio.sleep(follow.retry_after)
        logger.warning("Рассылка: альбом ушёл, а текст с кнопкой — нет", chat_id=chat_id, error=follow.error)

    async def upload(
        self, chat_id: int, media_type: MediaType, file_name: str, content_type: str, file: BinaryIO
    ) -> tuple[SendResult, Optional[Media]]:
        data = {"chat_id": str(chat_id), "disable_notification": "true"}
        if media_type == MediaType.VIDEO:
            data["supports_streaming"] = "true"
        files = {media_type.value: (file_name, file, content_type)}
        result, message = await self._call(_METHOD_BY_MEDIA[media_type], self._upload_timeout, data=data, files=files)
        if not result.ok:
            return result, None
        media = extract_media(message, media_type, file_name)
        if media is None:
            return SendResult(DeliveryStatus.FAILED, error="Telegram не вернул file_id"), None
        return SendResult(DeliveryStatus.SENT, message_id=message.get("message_id")), media

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        result, _ = await self._call("deleteMessage", self._timeout, json={"chat_id": chat_id, "message_id": message_id})
        if not result.ok:
            logger.info("Рассылка: не удалось удалить служебное сообщение", chat_id=chat_id, error=result.error)

    async def pin(self, chat_id: int, message_id: int) -> None:
        payload = {"chat_id": chat_id, "message_id": message_id, "disable_notification": True}
        result, _ = await self._call("pinChatMessage", self._timeout, json=payload)
        if not result.ok:
            logger.info("Рассылка: не удалось закрепить сообщение", chat_id=chat_id, error=result.error)

