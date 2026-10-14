"""Парсер метаданных FanFiction.net."""

from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from app.services.parsers.base import ParsedWork, WorkPageUnavailableError
from app.services.works import parse_work_status

logger = logging.getLogger(__name__)


def parse(html: str, url: str) -> ParsedWork:
    """Извлекает метаданные FanFiction.net из HTML."""
    soup = BeautifulSoup(html, "lxml")

    title_el = soup.select_one("#profile_top b.xcontrast_txt") or soup.select_one(
        "#profile_top > b"
    )
    if not title_el:
        raise WorkPageUnavailableError(f"FanFiction.net: на странице нет работы ({url})")
    title = title_el.get_text(strip=True)

    author_el = soup.select_one("#profile_top a[href*='/u/']")
    author = author_el.get_text(strip=True) if author_el else None

    meta_el = soup.select_one("#profile_top .xgray") or soup.select_one(
        "#profile_top span.xgray"
    )
    meta_text = meta_el.get_text(" ", strip=True) if meta_el else ""

    status = None
    if re.search(r"Complete", meta_text, re.I):
        status = parse_work_status("completed")
    elif meta_text:
        status = parse_work_status("in progress")

    words = None
    words_match = re.search(r"Words:\s*([\d,]+)", meta_text, re.I)
    if words_match:
        digits = words_match.group(1).replace(",", "")
        words = int(digits) if digits.isdigit() else None

    fandom = None
    pretitle = soup.select_one("#pre_story_links") or soup.select_one(
        "div#pre_story_links"
    )
    if pretitle:
        fandom = pretitle.get_text(" ", strip=True) or None

    logger.info("FFN разобран: «%s» автор=%s", title, author)
    return ParsedWork(
        title=title,
        author=author,
        status=status,
        word_count=words,
        tags=[],
        fandom=fandom,
        pairing=None,
        source_url=url,
        platform="ffn",
    )
