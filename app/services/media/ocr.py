"""OCR через OpenAI Vision (gpt-4o-mini), батчами с паузами и retry на 429."""

from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
import re
from pathlib import Path

from openai import AsyncOpenAI, RateLimitError as OpenAIRateLimitError

from app.config import get_settings
from app.proxy import openai_client
from app.services.media.errors import RateLimitError

logger = logging.getLogger(__name__)

OCR_PROMPT = (
    "Извлеки весь читаемый текст с изображений ниже (это кадры одного ролика). "
    "Особенно важны названия фанфиков, имена авторов, списки рекомендаций. "
    "Верни только распознанный текст без комментариев. "
    "Если текста нет — верни пустую строку."
)

_BATCH_SIZE = 4
_BATCH_PAUSE_SEC = 1.0
_RETRY_AFTER_RE = re.compile(r"try again in ([0-9.]+)\s*s", re.I)


def _image_data_url(path: Path) -> str:
    """Кодирует файл изображения в data URL для OpenAI Vision."""
    mime, _ = mimetypes.guess_type(str(path))
    if mime not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
        mime = "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _retry_delay(exc: BaseException, fallback: float = 3.0) -> float:
    """Достаёт паузу из текста 429 или возвращает fallback."""
    match = _RETRY_AFTER_RE.search(str(exc))
    if match:
        try:
            return max(1.0, float(match.group(1)))
        except ValueError:
            pass
    return fallback


def _is_rate_limit(exc: BaseException) -> bool:
    if isinstance(exc, (OpenAIRateLimitError, RateLimitError)):
        return True
    text = str(exc).lower()
    return "rate limit" in text or "429" in text or "tokens per min" in text


async def _ocr_batch(client: AsyncOpenAI, model: str, paths: list[Path]) -> str:
    """Один Vision-запрос на пачку кадров."""
    content: list[dict] = [{"type": "text", "text": OCR_PROMPT}]
    for path in paths:
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": _image_data_url(path),
                    "detail": "high",
                },
            }
        )

    delays = (0.0, 2.0, 5.0)
    last_exc: BaseException | None = None
    for attempt, base_delay in enumerate(delays):
        if base_delay:
            wait = base_delay
            if last_exc:
                wait = max(wait, _retry_delay(last_exc, fallback=base_delay))
            logger.warning(
                "OCR rate limit — повтор через %.1f с (попытка %s, кадров=%s)",
                wait,
                attempt + 1,
                len(paths),
            )
            await asyncio.sleep(wait)
        try:
            response = await client.chat.completions.create(
                model=model,
                temperature=0,
                messages=[{"role": "user", "content": content}],
            )
            text = (response.choices[0].message.content or "").strip()
            logger.info(
                "OCR батч %s кадр(ов): %s символов",
                len(paths),
                len(text),
            )
            return text
        except Exception as exc:
            last_exc = exc
            if _is_rate_limit(exc) and attempt < len(delays) - 1:
                continue
            if _is_rate_limit(exc):
                raise RateLimitError(
                    "Сервис распознавания перегружен (OCR rate limit)"
                ) from exc
            logger.exception(
                "Ошибка OpenAI Vision для батча %s",
                [p.name for p in paths],
            )
            return ""

    if last_exc and _is_rate_limit(last_exc):
        raise RateLimitError(
            "Сервис распознавания перегружен (OCR rate limit)"
        ) from last_exc
    return ""


async def ocr_images(image_paths: list[Path]) -> str:
    """Распознаёт текст на изображениях батчами через OpenAI Vision."""
    if not image_paths:
        return ""

    settings = get_settings()
    if not settings.openai_api_key:
        logger.warning("OPENAI_API_KEY не задан — OCR недоступен")
        return ""

    client = openai_client()
    model = settings.openai_vision_model
    chunks: list[str] = []

    for i in range(0, len(image_paths), _BATCH_SIZE):
        batch = image_paths[i : i + _BATCH_SIZE]
        if i > 0:
            await asyncio.sleep(_BATCH_PAUSE_SEC)
        text = await _ocr_batch(client, model, batch)
        if text:
            chunks.append(text)

    return "\n\n".join(chunks)
