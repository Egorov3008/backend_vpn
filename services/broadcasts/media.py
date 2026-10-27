"""Как отправить загруженный админом файл: тип по MIME/расширению и лимиты Bot API.

Bot API принимает при загрузке фото до 10 МБ, остальные файлы до 50 МБ. Фото
крупнее 10 МБ уходит файлом (document), остальное крупнее 50 МБ не принимаем.
"""
from pathlib import PurePosixPath
from typing import Optional, Sequence

from services.broadcasts.models import MediaType

MB = 1024 * 1024
PHOTO_MAX_BYTES = 10 * MB
FILE_MAX_BYTES = 50 * MB

_PHOTO_TYPES = {"image/jpeg", "image/png", "image/webp"}
_VIDEO_EXT = {".mp4", ".mov", ".m4v"}
_AUDIO_EXT = {".mp3", ".m4a", ".ogg", ".oga", ".wav", ".flac"}
_PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp"}


class MediaTooLarge(ValueError):
    pass


def detect_media_type(content_type: Optional[str], file_name: str, size: int, as_document: bool = False) -> MediaType:
    """Тип отправки для файла. ``as_document`` — отправить как файл без сжатия."""
    if size > FILE_MAX_BYTES:
        raise MediaTooLarge(f"Файл больше {FILE_MAX_BYTES // MB} МБ: Telegram не примет его от бота")
    if as_document:
        return MediaType.DOCUMENT
    ctype = (content_type or "").split(";")[0].strip().lower()
    ext = PurePosixPath(file_name or "").suffix.lower()
    if ctype == "image/gif" or ext == ".gif":
        return MediaType.ANIMATION
    if ctype in _PHOTO_TYPES or (not ctype.startswith(("video/", "audio/")) and ext in _PHOTO_EXT):
        return MediaType.PHOTO if size <= PHOTO_MAX_BYTES else MediaType.DOCUMENT
    if ctype.startswith("video/") or ext in _VIDEO_EXT:
        return MediaType.VIDEO
    if ctype.startswith("audio/") or ext in _AUDIO_EXT:
        return MediaType.AUDIO
    return MediaType.DOCUMENT


ALBUM_MAX = 10
_ALBUM_VISUAL = {MediaType.PHOTO, MediaType.VIDEO}


def album_error(types: Sequence[MediaType]) -> Optional[str]:
    """Почему эти файлы нельзя отправить одним альбомом (правила sendMediaGroup) или None.

    Фото и видео смешиваются; файлы — только с файлами, аудио — только с аудио; GIF в альбом нельзя."""
    if len(types) <= 1:
        return None
    if len(types) > ALBUM_MAX:
        return f"В альбоме до {ALBUM_MAX} файлов"
    kinds = set(types)
    if MediaType.ANIMATION in kinds:
        return "GIF нельзя добавить в альбом: отправьте его отдельной рассылкой или файлом"
    if kinds <= _ALBUM_VISUAL or kinds == {MediaType.DOCUMENT} or kinds == {MediaType.AUDIO}:
        return None
    return "В одном альбоме: фото и видео вместе, файлы только с файлами, аудио только с аудио"
