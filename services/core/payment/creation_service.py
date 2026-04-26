from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton

from bot_project import bot
from config import SUPPORT_CHAT_URL
from logger import logger

from services.core.keys.utils.create_key import CreateKey
from services.core.payment.processor import PaymentProcessor


class KeyCreationService:
    """Сервис создания ключа после оплаты."""

    def __init__(self, processor: PaymentProcessor, create_key: CreateKey):
        self.processor = processor
        self.create_key = create_key
        self.builder = InlineKeyboardBuilder()
        self.builder.add(
            InlineKeyboardButton(text="Техническая поддержка", url=SUPPORT_CHAT_URL),
            InlineKeyboardButton(text="Личный кабинет", callback_data="profile")
        )

    async def process(self, tariff_id: str = None):
        """Создаёт ключ и отправляет пользователю."""
        try:
            if tariff_id is None:
                operation, tariff_id = self.processor.extract_operation()
                if operation != "create_key":
                    raise ValueError(
                        f"Ожидалась операция 'create_key', получено: {operation}"
                    )

            tariff = await self.processor._model_service.tariffs.get_data(
                int(tariff_id)
            )
            user = await self.processor._model_service.users.get_data(
                self.processor.tg_id
            )

            logger.info(
                "[Цена:CreateKey] Создание ключа после оплаты",
                tg_id=self.processor.tg_id,
                tariff_id=tariff_id,
                tariff_amount=tariff.amount if tariff else None,
                number_of_months=self.processor.number_of_months,
                paid_amount=self.processor.amount,
            )

            key_data = await self.create_key.proces(
                tg_id=self.processor.tg_id,
                tariff=tariff,
                server_id=user.server_id,
                conn=self.processor._conn,
                number_of_months=self.processor.number_of_months,
            )

            if not key_data:
                raise ValueError("Не удалось создать ключ")

            logger.info(
                "[Цена:CreateKey] Ключ успешно создан",
                tg_id=self.processor.tg_id,
                tariff_id=tariff_id,
            )

        except Exception as e:
            logger.error(
                "Ошибка при создании ключа",
                error_type=type(e).__name__,
                error_message=str(e),
                tg_id=self.processor.tg_id,
                exc_info=True,
            )
            raise

        # Отправка сообщения — не критична для платёжного потока
        try:
            await self._send_key_message(key_data)
        except Exception as e:
            logger.warning(
                "Не удалось отправить сообщение с ключом",
                tg_id=self.processor.tg_id,
                error=str(e),
            )

    async def _send_key_message(self, key_data):
        """Отправляет сообщение с ключом."""
        message = (
            f"<b>Ссылка внизу - твой новый ключ! Скопируй его:</b>\n\n"
            f"{key_data.get('public_link', 'Недоступно')}\n\n"
            f"- Теперь перейди в приложение \n"
            f"- Нажми ➕!\n"
            f"- Далее ➡️ 'Добавить из буфера обмена'\n\n"
            f"⏳ Осталось дней: {key_data.get('days', 'Неизвестно')} 📅\n\n"
            f"<b>-Подробная инструкция внизу</b> 👇"
        )
        await bot.send_message(
            chat_id=self.processor.tg_id,
            text=message,
            reply_markup=self.builder.as_markup(),
        )
