from models.price_model.price import Price, Number
from models.stocks.stock import Stock
from logger import logger


class Pricing:
    """Формирование цены"""

    def formating(self, price: Price, stock: Stock) -> Number:
        if not stock:
            logger.debug(
                "[Цена] Скидка не применена — stock отсутствует",
                original_amount=price.amount,
            )
            return price.amount

        temp_price = Price(
            amount=price.amount, stock=stock.value, type_stock=stock.stock_type
        )
        result = temp_price.format_price
        logger.info(
            "[Цена] Скидка применена",
            original_amount=price.amount,
            stock_value=stock.value,
            stock_type=stock.stock_type,
            final_amount=result,
        )
        return result
