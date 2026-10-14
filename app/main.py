"""Точка входа Telegram-бота FicBrain."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Нужно до импортов app.*, если файл запускают как python app/main.py
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app.config import get_settings
from app.db.session import init_db
from app.handlers import setup_routers
from app.logging_config import setup_logging
from app.proxy import apply_proxy_env, create_bot

logger = logging.getLogger(__name__)


async def main() -> None:
    """Запускает long-polling бота."""
    setup_logging()
    apply_proxy_env()
    settings = get_settings()
    Path(settings.tmp_dir).mkdir(parents=True, exist_ok=True)

    if not settings.bot_token:
        raise RuntimeError("Не задан BOT_TOKEN в окружении / .env")

    logger.info("Инициализация базы данных…")
    await init_db()

    bot = create_bot()
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(setup_routers())

    logger.info("FicBrain bot запущен")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        logger.info("FicBrain bot остановлен")


if __name__ == "__main__":
    asyncio.run(main())
