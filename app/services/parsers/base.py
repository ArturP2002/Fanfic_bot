"""Общий интерфейс парсеров прямых ссылок."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field

from app.proxy import httpx_client

logger = logging.getLogger(__name__)

AO3_RE = re.compile(r"https?://(www\.)?archiveofourown\.org/works/\d+", re.I)
FICBOOK_RE = re.compile(r"https?://(www\.)?ficbook\.net/readfic/[\w-]+", re.I)
FFN_RE = re.compile(
    r"https?://(www\.)?fanfiction\.net/s/\d+",
    re.I,
)


class WorkPageUnavailableError(Exception):
    """На странице нет работы: вход по логину (закрытые работы AO3), удалена, капча."""


@dataclass
class ParsedWork:
    """Метаданные произведения, полученные по прямой ссылке."""

    title: str
    author: str | None = None
    status: str | None = None
    word_count: int | None = None
    tags: list[str] = field(default_factory=list)
    fandom: str | None = None
    pairing: str | None = None
    source_url: str | None = None
    platform: str | None = None
    raw_summary: str | None = None
    # Для переводов: ссылка на оригинал, указанная переводчиком
    original_url: str | None = None


_CANONICAL_URLS = (
    (re.compile(r"archiveofourown\.org/works/(\d+)", re.I), "https://archiveofourown.org/works/{}"),
    (re.compile(r"ficbook\.net/readfic/([\w-]+)", re.I), "https://ficbook.net/readfic/{}"),
    (re.compile(r"fanfiction\.net/s/(\d+)", re.I), "https://www.fanfiction.net/s/{}"),
)


def canonical_url(url: str) -> str:
    """Единый вид ссылки на работу: без глав, якорей, параметров и слеша в конце."""
    for pattern, template in _CANONICAL_URLS:
        match = pattern.search(url)
        if match:
            return template.format(match.group(1))
    return url.strip()


def detect_platform(url: str) -> str | None:
    """Определяет поддерживаемую площадку по URL."""
    if AO3_RE.search(url):
        return "ao3"
    if FICBOOK_RE.search(url):
        return "ficbook"
    if FFN_RE.search(url):
        return "ffn"
    return None


FETCH_ATTEMPTS = 3
FETCH_RETRY_DELAY_SECONDS = 2.0


def _is_retryable(exc: Exception) -> bool:
    """Временные сбои площадки (Cloudflare 52x, 429, обрыв сети) стоит повторить."""
    import httpx

    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or code >= 500
    return isinstance(exc, httpx.TransportError)


async def fetch_html(
    url: str,
    *,
    timeout: float = 30.0,
    attempts: int = FETCH_ATTEMPTS,
    user_agent: str | None = None,
    referer: str | None = None,
) -> str:
    """Загружает HTML страницы, повторяя запрос при временных сбоях."""
    import httpx

    headers = {
        "User-Agent": user_agent
        or (
            "Mozilla/5.0 (compatible; FicBrainBot/1.0; +https://t.me/; metadata only)"
        ),
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru,en;q=0.9",
    }
    if referer:
        headers["Referer"] = referer
    max_attempts = max(1, attempts)
    async with httpx_client(timeout=timeout) as client:
        for attempt in range(1, max_attempts + 1):
            try:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                if attempt == max_attempts or not _is_retryable(exc):
                    raise
                logger.warning(
                    "Временная ошибка загрузки %s (попытка %s/%s): %s",
                    url,
                    attempt,
                    max_attempts,
                    exc,
                )
                await asyncio.sleep(FETCH_RETRY_DELAY_SECONDS * attempt)
                continue
            logger.info("Страница загружена: %s status=%s", url, response.status_code)
            return response.text
    raise RuntimeError("unreachable")


async def parse_fic_url(url: str) -> ParsedWork | None:
    """Парсит поддерживаемую ссылку на фанфик."""
    platform = detect_platform(url)
    if not platform:
        logger.warning("Неподдерживаемая площадка для URL: %s", url)
        return None

    from app.services.parsers import ao3, ficbook, ffn

    html = await fetch_html(url)
    url = canonical_url(url)
    if platform == "ao3":
        return ao3.parse(html, url)
    if platform == "ficbook":
        return ficbook.parse(html, url)
    if platform == "ffn":
        return ffn.parse(html, url)
    return None
