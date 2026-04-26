"""
Генератор отчётов по ключам для администратора.

Использует сегментацию ключей для создания аналитических отчётов.
"""

from typing import List, Dict, Any

from models import Key
from services.core.keys.segmentation import KeySegmentationService
from services.core.segmentation.key_model import KeySegment


class KeyAdminReport:
    """Генератор отчётов по ключам для администраторских функций."""

    async def get_summary_stats(self, keys: List[Key]) -> Dict[str, Any]:
        """
        Получить сводную статистику по ключам.

        Args:
            keys: Список всех ключей

        Returns:
            Словарь со статистикой
        """
        distribution = await KeySegmentationService().segment_keys(keys)

        # Суммируем 10h + 24h — first-match сегментация разделяет их
        expiring_24h_count = (
            len(distribution.get(KeySegment.EXPIRING_10H, []))
            + len(distribution.get(KeySegment.EXPIRING_24H, []))
        )

        return {
            "total": len(keys),
            "active": len(distribution.get(KeySegment.ACTIVE, [])),
            "trial": len(distribution.get(KeySegment.TRIAL, [])),
            "expiring_24h": expiring_24h_count,
            "expiring_7d": len(distribution.get(KeySegment.EXPIRING_7D, [])),
            "expiring_30d": len(distribution.get(KeySegment.EXPIRING_30D, [])),
            "unused": len(distribution.get(KeySegment.UNUSED, [])),
            "expired": len(distribution.get(KeySegment.EXPIRED, [])),
            "other": len(distribution.get(KeySegment.ALL, [])),
        }

    async def get_keys_by_filter(self, keys: List[Key], filter_name: str) -> List[Key]:
        """
        Получить ключи по названию фильтра.

        Args:
            keys: Список всех ключей
            filter_name: Название фильтра ('expiring_24h', 'expired', 'all')

        Returns:
            Отфильтрованные ключи
        """
        return await KeySegmentationService().filter_by_name(keys, filter_name)

    async def format_report_text(self, keys: List[Key]) -> str:
        """
        Форматировать отчёт в текстовое сообщение.

        Args:
            keys: Список ключей

        Returns:
            Текстовый отчёт для отправки в Telegram
        """
        stats = await self.get_summary_stats(keys)

        message = (
            "📊 <b>Отчёт по ключам</b>\n\n"
            f"📈 <b>Статистика:</b>\n"
            f"• Всего ключей: <b>{stats['total']}</b>\n"
            f"• Активных: <b>{stats['active']}</b>\n"
            f"• Trial: <b>{stats['trial']}</b>\n"
            f"• Неиспользуемых: <b>{stats['unused']}</b>\n"
            f"• Прочих: <b>{stats['other']}</b>\n\n"
            f"⚠️ <b>Требуют внимания:</b>\n"
            f"• Истекают в 24ч: <b>{stats['expiring_24h']}</b>\n"
            f"• Истекают в 7д: <b>{stats['expiring_7d']}</b>\n"
            f"• Истекают в 30д: <b>{stats['expiring_30d']}</b>\n"
            f"• Истёкших: <b>{stats['expired']}</b>\n"
        )

        return message

    async def get_expiring_24h_details(self, keys: List[Key]) -> str:
        """
        Получить детали по ключам, истекающим в 24 часа.

        Args:
            keys: Список всех ключей

        Returns:
            Форматированный список ключей
        """
        expiring = await KeySegmentationService().get_expiring_24h(keys)

        if not expiring:
            return "✅ Нет ключей, истекающих в ближайшие 24 часа"

        message = f"⏰ <b>Ключи, истекающие в 24 часа:</b> ({len(expiring)})\n\n"

        for key in expiring[:10]:  # Показываем первые 10
            message += (
                f"📧 <code>{key.email}</code>\n"
                f"👤 tg_id: <code>{key.tg_id}</code>\n"
                f"⏱️ Истекает: <code>{key.expiry_time}</code>\n"
                f"─" * 30 + "\n"
            )

        if len(expiring) > 10:
            message += f"\n...и ещё {len(expiring) - 10} ключей"

        return message

    async def get_expired_details(self, keys: List[Key]) -> str:
        """
        Получить детали по истёкшим ключам.

        Args:
            keys: Список всех ключей

        Returns:
            Форматированный список ключей
        """
        expired = await KeySegmentationService().get_expired(keys)

        if not expired:
            return "✅ Нет истёкших ключей"

        message = f"🔴 <b>Истёкшие ключи:</b> ({len(expired)})\n\n"

        for key in expired[:10]:  # Показываем первые 10
            message += (
                f"📧 <code>{key.email}</code>\n"
                f"👤 tg_id: <code>{key.tg_id}</code>\n"
                f"⏱️ Истекал: <code>{key.expiry_time}</code>\n"
                f"─" * 30 + "\n"
            )

        if len(expired) > 10:
            message += f"\n...и ещё {len(expired) - 10} ключей"

        return message
