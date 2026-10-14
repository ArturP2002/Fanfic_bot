"""Кратковременное хранение результатов пакетной обработки для callback-кнопок."""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from redis.asyncio import Redis

from app.config import get_settings

logger = logging.getLogger(__name__)

_memory: dict[str, tuple[float, dict[str, Any]]] = {}
TTL_SECONDS = 60 * 60 * 6


def _serialize_batch(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


async def save_batch(payload: dict[str, Any]) -> str:
    """Сохраняет пакет и возвращает токен."""
    token = uuid.uuid4().hex[:16]
    payload = {**payload, "created_at": time.time()}
    raw = _serialize_batch(payload)
    settings = get_settings()
    try:
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        await redis.setex(f"ficbrain:batch:{token}", TTL_SECONDS, raw)
        await redis.aclose()
        logger.info("Пакет сохранён в Redis token=%s", token)
    except Exception:
        logger.warning("Redis недоступен — пакет сохранён в памяти token=%s", token)
        _memory[token] = (time.time() + TTL_SECONDS, payload)
    return token


async def load_batch(token: str) -> dict[str, Any] | None:
    """Загружает пакет по токену."""
    settings = get_settings()
    try:
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        raw = await redis.get(f"ficbrain:batch:{token}")
        await redis.aclose()
        if raw:
            return json.loads(raw)
    except Exception:
        logger.warning("Не удалось прочитать пакет из Redis token=%s", token)

    item = _memory.get(token)
    if not item:
        return None
    expires, payload = item
    if time.time() > expires:
        _memory.pop(token, None)
        return None
    return payload


def batch_from_resolution(user_id: int, batch) -> dict[str, Any]:
    """Сериализует ResolutionBatch для хранения."""
    items = []
    for item in batch.items:
        items.append(
            {
                "title": item.extracted.title,
                "author": item.extracted.author,
                "confidence": item.extracted.confidence,
                "work_id": item.work.id if item.work else None,
                "library_status": item.reading_status,
                "match_confidence": item.match_confidence,
                "needs_user_choice": item.needs_user_choice,
                "candidate_work_ids": item.candidate_work_ids,
                "is_new_for_catalog": item.is_new_for_catalog,
                "needs_catalog_confirm": item.needs_catalog_confirm,
            }
        )
    return {"user_id": user_id, "items": items}
