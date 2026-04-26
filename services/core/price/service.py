from typing import List, Optional

from models import Tariff
from models.stocks.stock import Stock
from services.core.data.service import ServiceDataModel
from services.core.price.form_price import Pricing
from services.core.price.result import PriceResult


class PriceService:
    """Единый сервис расчёта цены тарифа со скидкой."""

    def __init__(self, pricing: Pricing, model_data: ServiceDataModel):
        self._pricing = pricing
        self._stock_data = model_data.stocks

    async def calculate(self, tg_id: int, tariff: Tariff) -> PriceResult:
        """Один тариф для пользователя."""
        stock = await self._stock_data.get_data(tg_id)
        return self._apply(tariff, stock)

    async def calculate_batch(
        self, tg_id: int, tariffs: List[Tariff]
    ) -> dict[int, PriceResult]:
        """Все тарифы за один запрос Stock."""
        stock = await self._stock_data.get_data(tg_id)
        return {t.id: self._apply(t, stock) for t in tariffs}

    def calculate_sync(self, tariff: Tariff, stock: Optional[Stock]) -> PriceResult:
        """Когда Stock уже загружен."""
        return self._apply(tariff, stock)

    def _apply(self, tariff: Tariff, stock: Optional[Stock]) -> PriceResult:
        if not stock or not stock.is_valid:
            return PriceResult(
                original_amount=tariff.amount,
                final_amount=tariff.amount,
                stock_value=0,
                stock_type="",
                has_discount=False,
            )
        from models.price_model.price import Price

        price = Price(amount=tariff.amount)
        final = self._pricing.formating(price=price, stock=stock)
        return PriceResult(
            original_amount=tariff.amount,
            final_amount=final,
            stock_value=stock.value,
            stock_type=stock.stock_type,
            has_discount=True,
        )