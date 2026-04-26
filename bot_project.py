class _BotStub:
    """Stub for bot_project.bot in backend context.
    Telegram notifications are sent via httpx directly to Bot API.
    """

    async def send_message(self, *args: object, **kwargs: object) -> None:
        pass

    async def send_document(self, *args: object, **kwargs: object) -> None:
        pass

    async def send_photo(self, *args: object, **kwargs: object) -> None:
        pass


bot = _BotStub()
