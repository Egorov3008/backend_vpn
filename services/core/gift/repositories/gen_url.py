from config import BOT_NAME


class GiftUrlGenerator:
    """Отвечает только за генерацию URL"""

    def generate(self, token: str, bot_name=BOT_NAME) -> str:
        """Генерирует URL для подарочной ссылки"""
        return f"https://t.me/{bot_name}?start={token}"
