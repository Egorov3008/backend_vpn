"""
Тесты для системы уведомлений (INotifier и реализации).
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from services.core.notifications.protocols import INotifier, NoOpNotifier
from services.core.notifications.telegram_notifier import TelegramBotNotifier


class TestNoOpNotifier:
    """Тесты для Null-object нотификатора."""

    @pytest.mark.asyncio
    async def test_noop_notifier_does_nothing(self):
        """NoOpNotifier не отправляет никакие уведомления."""
        notifier = NoOpNotifier()

        # Не должно вызывать ошибок
        await notifier.send_key_created(
            tg_id=123456,
            key_data={"public_link": "https://example.com/key"},
        )

        await notifier.send_key_renewed(
            tg_id=123456,
            email="test@example.com",
            new_expiry="2026-06-07",
            traffic_limit_gb=10,
            tariff_name="Test",
        )

        await notifier.send_payment_received(
            tg_id=123456,
            amount=99.99,
            payment_id="pay_123",
        )


class TestTelegramBotNotifier:
    """Тесты для Telegram нотификатора."""

    @pytest.mark.asyncio
    async def test_send_message_without_token(self):
        """Отправка без токена не вызывает ошибок."""
        notifier = TelegramBotNotifier(bot_token="")

        # Не должно вызывать ошибок
        await notifier.send_message(123456, "Test message")

    @pytest.mark.asyncio
    async def test_send_message_with_invalid_chat_id(self):
        """Некорректный chat_id не вызывает ошибок."""
        notifier = TelegramBotNotifier(bot_token="test_token")

        # Передаем некорректный chat_id - не должно вызывать ошибок
        await notifier.send_message("invalid", "Test message")

    @pytest.mark.asyncio
    async def test_send_key_created(self):
        """send_key_created формирует правильное сообщение."""
        notifier = TelegramBotNotifier(
            bot_token="test_token",
            support_chat_url="https://t.me/support",
        )

        key_data = {
            "public_link": "https://example.com/key",
            "days": 30,
        }

        with patch.object(notifier, "send_message", new_callable=AsyncMock) as mock_send:
            await notifier.send_key_created(tg_id=123456, key_data=key_data)

            mock_send.assert_called_once()
            call_args = mock_send.call_args

            # Проверяем tg_id
            assert call_args.args[0] == 123456

            # Проверяем текст сообщения
            message = call_args.args[1]
            assert "Ссылка внизу - твой новый ключ!" in message
            assert "https://example.com/key" in message
            assert "30" in message  # days

    @pytest.mark.asyncio
    async def test_send_key_renewed(self):
        """send_key_renewed формирует правильное сообщение."""
        notifier = TelegramBotNotifier(
            bot_token="test_token",
            support_chat_url="https://t.me/support",
        )

        with patch.object(notifier, "send_message", new_callable=AsyncMock) as mock_send:
            await notifier.send_key_renewed(
                tg_id=123456,
                email="test@example.com",
                new_expiry="07.06.2026 12:00",
                traffic_limit_gb=50,
                tariff_name="Premium",
            )

            mock_send.assert_called_once()
            message = mock_send.call_args.args[1]

            assert "продлён" in message
            assert "test@example.com" in message
            assert "07.06.2026 12:00" in message
            assert "Premium" in message
            assert "50" in message  # GB


class TestKeyCreationServiceWithNotifier:
    """Тесты KeyCreationService с INotifier."""

    @pytest.mark.asyncio
    async def test_send_notification_calls_notifier(self):
        """send_notification() вызывает notifier.send_key_created()."""
        from services.core.payment.creation_service import KeyCreationService
        from services.core.notifications.protocols import INotifier

        # Моки
        processor = AsyncMock()
        processor.tg_id = 123456

        create_key = AsyncMock()
        notifier = AsyncMock(spec=INotifier)

        service = KeyCreationService(
            processor=processor,
            create_key=create_key,
            notifier=notifier,
        )

        key_data = {
            "public_link": "https://example.com/key",
            "days": 30,
        }

        # Вызываем send_notification отдельно
        await service.send_notification(key_data)

        # Проверяем что уведомление отправлено
        notifier.send_key_created.assert_called_once()
        call_args = notifier.send_key_created.call_args
        assert call_args.kwargs["tg_id"] == 123456
        assert "public_link" in call_args.kwargs["key_data"]

    @pytest.mark.asyncio
    async def test_send_notification_without_notifier(self):
        """send_notification() без notifier не падает."""
        from services.core.payment.creation_service import KeyCreationService

        processor = AsyncMock()
        processor.tg_id = 123456

        create_key = AsyncMock()

        # notifier = None (по умолчанию)
        service = KeyCreationService(
            processor=processor,
            create_key=create_key,
        )

        key_data = {"public_link": "https://example.com/key"}

        # Не должно вызывать ошибок
        await service.send_notification(key_data)

    @pytest.mark.asyncio
    async def test_process_returns_key_data(self):
        """process() возвращает key_data."""
        from services.core.payment.creation_service import KeyCreationService

        processor = AsyncMock()
        processor.tg_id = 123456
        processor._conn = AsyncMock()
        processor.extract_operation = MagicMock(return_value=("create_key", "1"))

        create_key = AsyncMock()
        create_key.proces = AsyncMock(return_value={
            "public_link": "https://example.com/key",
            "days": 30,
        })

        service = KeyCreationService(
            processor=processor,
            create_key=create_key,
        )

        key_data = await service.process(tariff_id="1")
        assert key_data is not None
        assert "public_link" in key_data


class TestKeyRenewalServiceWithNotifier:
    """Тесты KeyRenewalService с INotifier."""

    @pytest.mark.asyncio
    async def test_send_notification_calls_notifier(self):
        """send_notification() вызывает notifier.send_key_renewed()."""
        from services.core.payment.renewal_service import KeyRenewalService
        from services.core.notifications.protocols import INotifier
        from datetime import datetime

        # Моки
        processor = AsyncMock()
        processor.tg_id = 123456

        key_manager = AsyncMock()
        notifier = AsyncMock(spec=INotifier)

        service = KeyRenewalService(
            processor=processor,
            key_manager=key_manager,
            notifier=notifier,
        )

        updated_key = MagicMock()
        updated_key.email = "test@example.com"
        updated_key.name_tariff = "Premium"
        new_expiry = datetime.now()
        traffic_gb = 50

        # Вызываем send_notification отдельно
        await service.send_notification(
            updated_key=updated_key,
            new_expiry=new_expiry,
            traffic_gb=traffic_gb,
        )

        # Проверяем что уведомление отправлено
        notifier.send_key_renewed.assert_called_once()
        call_args = notifier.send_key_renewed.call_args
        assert call_args.kwargs["tg_id"] == 123456
        assert call_args.kwargs["email"] == "test@example.com"
        assert call_args.kwargs["tariff_name"] == "Premium"
        assert call_args.kwargs["traffic_limit_gb"] == 50

    @pytest.mark.asyncio
    async def test_send_notification_without_notifier(self):
        """send_notification() без notifier не падает."""
        from services.core.payment.renewal_service import KeyRenewalService
        from datetime import datetime

        processor = AsyncMock()
        processor.tg_id = 123456
        key_manager = AsyncMock()

        # notifier = None (по умолчанию)
        service = KeyRenewalService(
            processor=processor,
            key_manager=key_manager,
        )

        updated_key = MagicMock()
        updated_key.email = "test@example.com"
        new_expiry = datetime.now()
        traffic_gb = 50

        # Не должно вызывать ошибок
        await service.send_notification(
            updated_key=updated_key,
            new_expiry=new_expiry,
            traffic_gb=traffic_gb,
        )

    @pytest.mark.asyncio
    async def test_process_returns_renewal_data(self):
        """process() возвращает renewal_data."""
        from services.core.payment.renewal_service import KeyRenewalService
        from datetime import datetime

        processor = AsyncMock()
        processor.tg_id = 123456
        processor._conn = AsyncMock()
        processor._cache.tariffs.temporary_get = AsyncMock(return_value=None)
        processor.extract_operation = MagicMock(return_value=("renew_key", "test@example.com"))

        key_manager = AsyncMock()
        updated_key = MagicMock()
        updated_key.expiry_time = int(datetime.now().timestamp() * 1000)
        updated_key.name_tariff = "Premium"
        key_manager.extension_key = AsyncMock(return_value=updated_key)

        model_service = AsyncMock()
        key = MagicMock()
        key.tariff_id = 1
        model_service.keys.get_data = AsyncMock(return_value=key)
        model_service.tariffs.get_data = AsyncMock(return_value=MagicMock(id=1, amount=99.99))
        model_service.users.get_data = AsyncMock(return_value=MagicMock(server_id=1))
        model_service.servers.get_data = AsyncMock(return_value=None)
        processor._model_service = model_service

        service = KeyRenewalService(
            processor=processor,
            key_manager=key_manager,
        )

        result = await service.process(email="test@example.com")
        assert result is not None
        assert "updated_key" in result
        assert "new_expiry" in result
