from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton
from datetime import datetime

from bot_project import bot
from logger import logger
from services.core.payment.processor import PaymentProcessor
from services.metrics.registry import key_renewed_total


class KeyRenewalService:
    """Сервис продления ключа после оплаты."""

    def __init__(self, processor: PaymentProcessor, key_manager):
        self.processor = processor
        self.key_manager = key_manager
        self.builder = InlineKeyboardBuilder()
        self.builder.add(
            InlineKeyboardButton(text="Техническая поддержка", url="https://t.me/support_chat")
        )

    async def process(self, email: str = None):
        """Продлевает ключ и уведомляет пользователя."""
        try:
            if email is None:
                operation, email = self.processor.extract_operation()
                if operation != "renew_key":
                    raise ValueError(
                        f"Ожидалась операция 'renew_key', получено: {operation}"
                    )

            # Загрузка данных для продления (с DB-fallback если кеш miss)
            key = await self.processor._model_service.keys.get_data(email, self.processor._conn)

            # Проверяем, есть ли выбранный тариф в кеше (для продления пробного ключа)
            renewal_cache_key = f"renewal_tariff_{email}"
            selected_tariff_id = None
            try:
                cached_data = await self.processor._cache.tariffs.temporary_get(renewal_cache_key)
                if cached_data and isinstance(cached_data, dict):
                    selected_tariff_id = cached_data.get("tariff_id")
            except Exception:
                pass

            # Используем выбранный тариф из кеша или fallback на tariff_id ключа
            tariff_id = selected_tariff_id if selected_tariff_id is not None else key.tariff_id
            tariff = await self.processor._model_service.tariffs.get_data(tariff_id, self.processor._conn)

            user = await self.processor._model_service.users.get_data(self.processor.tg_id, self.processor._conn)
            server = await self.processor._model_service.servers.get_data(user.server_id, self.processor._conn)

            logger.info(
                "[Цена:RenewKey] Продление ключа после оплаты",
                email=email,
                tg_id=self.processor.tg_id,
                tariff_id=tariff.id if tariff else None,
                tariff_amount=tariff.amount if tariff else None,
                number_of_months=self.processor.number_of_months,
                paid_amount=self.processor.amount,
                selected_tariff_id=selected_tariff_id,
                used_cache=selected_tariff_id is not None,
            )

            updated_key = await self.key_manager.extension_key(
                key=key,
                conn=self.processor._conn,
                server=server,
                tariff=tariff,
                number_of_months=self.processor.number_of_months,
            )

            new_expiry = datetime.fromtimestamp(updated_key.expiry_time / 1000)
            traffic_gb = round(updated_key.total_gb / (2**30))

            logger.info(
                "[Цена:RenewKey] Ключ продлён",
                email=email,
                new_expiry=new_expiry.isoformat(),
                traffic_gb=traffic_gb,
            )

            key_renewed_total.inc()

            # Очищаем кеш с выбранным тарифом после успешного продления
            if selected_tariff_id is not None:
                try:
                    await self.processor._cache.tariffs.delete(renewal_cache_key)
                    logger.info(
                        "[Цена:RenewKey] Кеш с выбранным тарифом очищен",
                        email=email,
                        renewal_cache_key=renewal_cache_key,
                    )
                except Exception as e:
                    logger.warning(
                        "[Цена:RenewKey] Не удалось очистить кеш тарифа",
                        email=email,
                        error=str(e),
                    )

        except Exception as e:
            logger.error(
                "Ошибка при продлении ключа",
                error_type=type(e).__name__,
                error_message=str(e),
                tg_id=self.processor.tg_id,
                exc_info=True,
            )
            raise

        # Отправка сообщения — не критична для платёжного потока
        try:
            await self._send_renewal_message(updated_key, new_expiry, traffic_gb)
        except Exception as e:
            logger.warning(
                "Не удалось отправить сообщение о продлении",
                tg_id=self.processor.tg_id,
                error=str(e),
            )

    async def _send_renewal_message(self, key_details, new_expiry, traffic_limit):
        """Отправляет сообщение о продлении."""
        await bot.send_message(
            key_details.tg_id,
            f"✅ Ваш ключ <a href='{key_details.key}'>{key_details.email}</a> продлён!\n\n"
            f"📅 До: <b>{new_expiry.strftime('%d.%m.%Y %H:%M')}</b>\n"
            f"По тарифу: <b>{key_details.name_tariff}</b>\n",
            reply_markup=self.builder.as_markup(),
        )
