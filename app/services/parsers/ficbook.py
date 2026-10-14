"""Парсер метаданных Ficbook."""

from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup, Tag

from app.services.parsers.base import ParsedWork, WorkPageUnavailableError
from app.services.works import parse_work_status

logger = logging.getLogger(__name__)


def _first_text(elements: list[Tag]) -> str | None:
    """Текст первого непустого элемента (ссылки-аватарки пустые)."""
    for el in elements:
        text = el.get_text(strip=True)
        if text:
            return text
    return None


def _original_author(soup: BeautifulSoup) -> str | None:
    """Автор оригинала у переводов (на странице перевода первым идёт переводчик)."""
    label = soup.find("strong", string=re.compile(r"Автор оригинала", re.I))
    if not label or not label.parent:
        return None
    return _first_text(label.parent.select("a"))


def _original_url(soup: BeautifulSoup) -> str | None:
    """Ссылка на оригинал у переводов (на странице это обычный текст, не <a>)."""
    label = soup.find("strong", string=re.compile(r"^\s*Оригинал\s*:", re.I))
    if not label or not label.parent:
        return None
    match = re.search(r"https?://\S+", label.parent.get_text(" ", strip=True))
    return match.group(0) if match else None


def parse(html: str, url: str) -> ParsedWork:
    """Извлекает метаданные Ficbook из HTML."""
    soup = BeautifulSoup(html, "lxml")

    title_el = (
        soup.select_one("h1.fanfic-main-info__title")
        or soup.select_one("h1")
        or soup.select_one(".fanfic-hat h1")
    )
    if not title_el:
        raise WorkPageUnavailableError(f"Ficbook: на странице нет работы ({url})")
    title = title_el.get_text(strip=True)

    author = _original_author(soup) or _first_text(
        soup.select("a[itemprop='author'], .creator-nickname, a[href*='/authors/']")
    )

    status = None
    status_text = soup.get_text(" ", strip=True).lower()
    if "в процессе" in status_text or "пишется" in status_text:
        status = parse_work_status("пишется")
    elif "заверш" in status_text:
        status = parse_work_status("завершён")

    words = None
    words_match = re.search(r"([\d\s]+)\s*слов", soup.get_text(" ", strip=True), re.I)
    if words_match:
        digits = re.sub(r"\D", "", words_match.group(1))
        words = int(digits) if digits else None

    fandom_el = soup.select_one("a[href*='/fanfiction/']")
    fandom = fandom_el.get_text(strip=True) if fandom_el else None

    pairing_el = soup.select_one(".pairing-link") or soup.select_one("a[href*='/pairings/']")
    pairing = pairing_el.get_text(strip=True) if pairing_el else None

    tag_els = soup.select(".tags a, .tag, a[href*='/tags/']")
    tags = [a.get_text(strip=True) for a in tag_els[:20] if a.get_text(strip=True)]

    logger.info("Ficbook разобран: «%s» автор=%s", title, author)
    return ParsedWork(
        title=title,
        author=author,
        status=status,
        word_count=words,
        tags=tags,
        fandom=fandom,
        pairing=pairing,
        source_url=url,
        platform="ficbook",
        original_url=_original_url(soup),
    )
