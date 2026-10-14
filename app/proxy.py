"""SOCKS5-прокси для исходящих запросов (VPS в РФ).

Задаётся через SOCKS5_PROXY, например:
  socks5://user:pass@host:1080
  socks5h://host:1080   # DNS через прокси (предпочтительно)
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

import httpx
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from openai import AsyncOpenAI

from app.config import get_settings

logger = logging.getLogger(__name__)


def proxy_url() -> str | None:
    """Нормализованный URL прокси или None, если прокси выключен."""
    raw = (get_settings().socks5_proxy or "").strip()
    if not raw:
        return None
    # socks5h — резолв DNS на стороне прокси (нужно из РФ)
    if raw.startswith("socks5://") and "socks5h://" not in raw:
        # не трогаем, если пользователь явно указал socks5://
        pass
    if not raw.startswith(("socks5://", "socks5h://", "http://", "https://")):
        raw = f"socks5h://{raw}"
    return raw


def apply_proxy_env() -> None:
    """Прописывает ALL_PROXY/HTTP(S)_PROXY для библиотек и subprocess (yt-dlp, Apify)."""
    url = proxy_url()
    if not url:
        logger.info("SOCKS5_PROXY не задан — исходящий трафик без прокси")
        return
    for key in ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        os.environ[key] = url
    # Локальные сервисы не гоняем через прокси
    no_proxy = os.environ.get("NO_PROXY", os.environ.get("no_proxy", ""))
    extras = "localhost,127.0.0.1,postgres,redis"
    merged = ",".join(part for part in (no_proxy, extras) if part)
    os.environ["NO_PROXY"] = merged
    os.environ["no_proxy"] = merged
    safe = url.split("@")[-1] if "@" in url else url
    logger.info("Прокси включён для исходящих запросов: %s", safe)


def httpx_client(**kwargs) -> httpx.AsyncClient:
    """AsyncClient httpx с прокси (если настроен)."""
    params = {"follow_redirects": True, **kwargs}
    url = proxy_url()
    if url:
        params["proxy"] = url
    return httpx.AsyncClient(**params)


@lru_cache
def _openai_http_client() -> httpx.AsyncClient | None:
    """Долгоживущий httpx-клиент для OpenAI (с прокси)."""
    url = proxy_url()
    if not url:
        return None
    return httpx.AsyncClient(
        proxy=url,
        timeout=httpx.Timeout(120.0, connect=30.0),
    )


def openai_client(*, api_key: str | None = None) -> AsyncOpenAI:
    """AsyncOpenAI с SOCKS5, если прокси задан."""
    settings = get_settings()
    key = api_key if api_key is not None else settings.openai_api_key
    http_client = _openai_http_client()
    if http_client is not None:
        return AsyncOpenAI(api_key=key, http_client=http_client)
    return AsyncOpenAI(api_key=key)


def create_bot(token: str | None = None) -> Bot:
    """Telegram Bot с aiohttp-сессией через SOCKS5 (если задан)."""
    settings = get_settings()
    bot_token = token or settings.bot_token
    url = proxy_url()
    if url:
        session = AiohttpSession(proxy=url)
        return Bot(token=bot_token, session=session)
    return Bot(token=bot_token)


def ytdlp_proxy_args() -> list[str]:
    """Аргументы yt-dlp для прокси."""
    url = proxy_url()
    if not url:
        return []
    return ["--proxy", url]
