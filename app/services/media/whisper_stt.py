"""Распознавание речи через OpenAI Whisper API."""

from __future__ import annotations

import logging
from pathlib import Path

from app.config import get_settings
from app.proxy import openai_client

logger = logging.getLogger(__name__)


async def transcribe_audio(audio_path: Path) -> str:
    """Транскрибирует аудиофайл. Без ключа возвращает пустую строку."""
    settings = get_settings()
    if not settings.openai_api_key:
        logger.warning("OPENAI_API_KEY не задан — транскрипция пропущена")
        return ""

    client = openai_client()
    logger.info("Отправка аудио в Whisper: %s", audio_path.name)
    with audio_path.open("rb") as fh:
        result = await client.audio.transcriptions.create(
            model="whisper-1",
            file=fh,
            language=settings.whisper_language or None,
        )
    text = (result.text or "").strip()
    logger.info("Транскрипт получен, длина=%s", len(text))
    return text
