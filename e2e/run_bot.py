"""Запуск Telegram-бота (репозиторий plaltform-bot) для e2e.

Код бота не меняется: aiogram-сессии бота подставляется адрес фейкового
Bot API из ``E2E_FAKES_URL``, остальное — как ``python main.py``.

Использование: ``python run_bot.py <путь к plaltform-bot>``.
"""
import asyncio
import os
import sys

BOT_REPO = os.path.abspath(sys.argv[1])
sys.path.insert(0, BOT_REPO)
os.chdir(BOT_REPO)

from aiogram.client.telegram import TelegramAPIServer  # noqa: E402

import bot_project  # noqa: E402

bot_project.bot.session.api = TelegramAPIServer.from_base(os.environ["E2E_FAKES_URL"].rstrip("/"))

import main  # noqa: E402

if __name__ == "__main__":
    asyncio.run(main.main())
