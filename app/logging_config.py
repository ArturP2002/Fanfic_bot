"""Настройка логирования приложения."""

import logging
import sys

from app.config import get_settings


def setup_logging() -> None:
    """Инициализирует корневой логгер с русскоязычными сообщениями в формате."""
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )
    logging.getLogger("aiogram").setLevel(logging.INFO)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.info("Логирование инициализировано, уровень=%s", settings.log_level)
