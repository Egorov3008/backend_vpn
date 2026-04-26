import asyncpg

from models import PaymentModel
from services.cache import CacheService
from services.core.data.service import ServiceDataModel
from logger import logger


class PaymentProcessor:
    """Общая логика обработки платежа: загрузка данных, валидация, обновление."""

    def __init__(
        self,
        conn: asyncpg.Pool,
        model_service: ServiceDataModel,
        cache: CacheService,
    ):
        self._conn = conn
        self._model_service = model_service
        self._cache = cache

        # Поля, заполняемые при обработке
        self.amount: float = None
        self.payment_type: str = None
        self.tg_id: int = None
        self.number_of_months: int = None
        self.discount_percent: int = 0
        self.referral_discount: float = 0.0
        self.status: str = None

    async def load_payment_data(self, payment_id: str):
        """Загружает данные платежа из БД."""
        logger.info(
            "[Цена:Processor] Загрузка данных платежа",
            payment_id=payment_id,
        )
        
        data: PaymentModel = await self._model_service.payments.get_data(payment_id)

        if not data:
            logger.error(
                "[Цена:Processor] Платёж не найден в БД",
                payment_id=payment_id,
            )
            raise ValueError(f"Платёж не найден: {payment_id}")

        self.amount = data.amount
        self.payment_type = data.payment_type
        self.tg_id = data.tg_id
        self.number_of_months = data.number_of_months
        self.discount_percent = data.discount_percent
        self.referral_discount = data.referral_discount
        self.status = data.status

        logger.info(
            "[Цена:Processor] Данные платежа загружены из БД",
            payment_id=payment_id,
            amount=data.amount,
            payment_type=data.payment_type,
            tg_id=data.tg_id,
            number_of_months=data.number_of_months,
            discount_percent=data.discount_percent,
            referral_discount=data.referral_discount,
            status=data.status,
        )

    async def update_payment(self, payment_id: str, status: str = "succeeded"):
        """Обновляет или создает платеж в БД (UPSERT)."""
        payment = PaymentModel(
            payment_id=payment_id,
            payment_type=self.payment_type,
            tg_id=self.tg_id,
            amount=self.amount,
            number_of_months=self.number_of_months or 1,
            discount_percent=self.discount_percent,
            status=status,
        )

        # Проверяем, существует ли платёж
        existing_payment = await self._model_service.payments.get_data(payment_id)

        if existing_payment:
            # UPDATE если существует
            await self._model_service.payments.update(
                self._conn, payment, search_data={"payment_id": payment_id}
            )
        else:
            # CREATE если не существует
            await self._model_service.payments.save_data(
                self._conn, payment, payment_id=payment_id
            )

    def extract_operation(self) -> list[str]:
        """Извлекает тип операции и данные из payment_type."""
        logger.info(
            "Приступаю к обработке процесса продления (создания) ключа",
            payment_type=self.payment_type,
        )
        if "|" not in self.payment_type:
            raise ValueError(f"Некорректный формат payment_type: {self.payment_type}")
        return self.payment_type.split("|", 1)
