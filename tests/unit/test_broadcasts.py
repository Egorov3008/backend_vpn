"""Чистая логика рассылок: тип отправки файла, разбор ответов Bot API, темп."""
import pytest

from services.broadcasts.media import FILE_MAX_BYTES, PHOTO_MAX_BYTES, MediaTooLarge, album_error, detect_media_type
from services.broadcasts.models import BroadcastMessage, Button, DeliveryStatus, Media, MediaType
from services.broadcasts.runner import Pacer
from services.broadcasts.telegram import build_requests, extract_media, parse_response


@pytest.mark.parametrize(
    "content_type, name, expected",
    [
        ("image/jpeg", "a.jpg", MediaType.PHOTO),
        ("", "a.PNG", MediaType.PHOTO),
        ("video/mp4", "clip.mp4", MediaType.VIDEO),
        ("video/quicktime", "IMG_0001.MOV", MediaType.VIDEO),
        ("application/octet-stream", "clip.mp4", MediaType.VIDEO),
        ("image/gif", "fun.gif", MediaType.ANIMATION),
        ("audio/mpeg", "song.mp3", MediaType.AUDIO),
        ("application/pdf", "rules.pdf", MediaType.DOCUMENT),
    ],
)
def test_media_type_by_file(content_type, name, expected):
    assert detect_media_type(content_type, name, 1000) == expected


def test_big_photo_goes_as_file_and_too_big_is_refused():
    assert detect_media_type("image/jpeg", "a.jpg", PHOTO_MAX_BYTES + 1) == MediaType.DOCUMENT
    with pytest.raises(MediaTooLarge):
        detect_media_type("video/mp4", "a.mp4", FILE_MAX_BYTES + 1)


def test_as_document_keeps_original_file():
    assert detect_media_type("image/jpeg", "a.jpg", 1000, as_document=True) == MediaType.DOCUMENT


def test_bot_api_answers():
    assert parse_response(200, {"ok": True, "result": {"message_id": 7}}).message_id == 7
    blocked = parse_response(403, {"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked by the user"})
    assert blocked.status == DeliveryStatus.BLOCKED
    assert parse_response(400, {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}).status == DeliveryStatus.BLOCKED
    flood = parse_response(429, {"ok": False, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 3}})
    assert flood.retry_after == 3
    assert parse_response(401, {"ok": False, "error_code": 401, "description": "Unauthorized"}).unauthorized
    assert parse_response(502, None).status == DeliveryStatus.FAILED


def test_media_message_uses_caption_and_web_app_button():
    message = BroadcastMessage(
        text="Подпись",
        media=(Media(MediaType.VIDEO, "file-id-123456"),),
        button=Button("miniapp", "Открыть", "https://app.example.test"),
    )

    [(method, payload)] = build_requests(42, message)

    assert method == "sendVideo"
    assert payload["video"] == "file-id-123456"
    assert payload["caption"] == "Подпись"
    assert payload["reply_markup"]["inline_keyboard"][0][0]["web_app"] == {"url": "https://app.example.test"}


def test_file_id_found_when_telegram_changes_type():
    # Видео не в MP4 Telegram может вернуть документом
    media = extract_media({"document": {"file_id": "doc-id"}}, MediaType.VIDEO, "a.avi")
    assert media == Media(MediaType.DOCUMENT, "doc-id", "a.avi")
    photo = extract_media({"photo": [{"file_id": "small"}, {"file_id": "big"}]}, MediaType.PHOTO, None)
    assert photo.file_id == "big"


async def test_pacer_spaces_requests():
    now = [0.0]
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    pacer = Pacer(rate=10, clock=lambda: now[0], sleep=sleep)
    for _ in range(5):
        await pacer.wait()

    assert now[0] == pytest.approx(0.4)


PHOTO = Media(MediaType.PHOTO, "photo-id-123456")
VIDEO = Media(MediaType.VIDEO, "video-id-123456")


def test_album_caption_goes_on_first_file():
    [(method, payload)] = build_requests(42, BroadcastMessage(text="Подпись", media=(PHOTO, VIDEO)))

    assert method == "sendMediaGroup"
    assert [m["media"] for m in payload["media"]] == [PHOTO.file_id, VIDEO.file_id]
    assert payload["media"][0]["caption"] == "Подпись"
    assert "caption" not in payload["media"][1]


def test_album_with_button_sends_text_separately():
    button = Button("url", "Сайт", "https://example.test")
    message = BroadcastMessage(text="Текст", media=(PHOTO, VIDEO), button=button)

    (album_method, album), (text_method, text) = build_requests(42, message)

    assert album_method == "sendMediaGroup"
    assert all("caption" not in m for m in album["media"])
    assert text_method == "sendMessage"
    assert text["text"] == "Текст"
    assert text["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://example.test"
    assert message.cost == 3


@pytest.mark.parametrize(
    "types, ok",
    [
        ([MediaType.PHOTO, MediaType.VIDEO, MediaType.PHOTO], True),
        ([MediaType.DOCUMENT, MediaType.DOCUMENT], True),
        ([MediaType.AUDIO, MediaType.AUDIO], True),
        ([MediaType.PHOTO, MediaType.DOCUMENT], False),
        ([MediaType.AUDIO, MediaType.VIDEO], False),
        ([MediaType.PHOTO, MediaType.ANIMATION], False),
        ([MediaType.PHOTO] * 11, False),
    ],
)
def test_album_rules(types, ok):
    assert (album_error(types) is None) == ok


def test_album_message_id_is_first_message():
    result = parse_response(200, {"ok": True, "result": [{"message_id": 5}, {"message_id": 6}]})
    assert result.message_id == 5
