"""Парсер метаданных Archive of Our Own."""

from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from app.services.parsers.base import ParsedWork, WorkPageUnavailableError
from app.services.works import parse_work_status

logger = logging.getLogger(__name__)


def parse(html: str, url: str) -> ParsedWork:
    """Извлекает метаданные AO3 из HTML."""
    soup = BeautifulSoup(html, "lxml")

    title_el = soup.select_one("h2.title.heading") or soup.select_one("h2.title")
    if not title_el:
        raise WorkPageUnavailableError(f"AO3: на странице нет работы ({url})")
    title = title_el.get_text(strip=True)

    author_el = soup.select_one("a[rel='author']")
    author = author_el.get_text(strip=True) if author_el else None

    # dd.status содержит дату, а «Completed:» / «Updated:» — в dt.status
    status = None
    status_label = soup.select_one("dt.status")
    label_text = status_label.get_text(strip=True).lower() if status_label else ""
    if label_text.startswith("completed"):
        status = parse_work_status("completed")
    elif label_text.startswith("updated"):
        status = parse_work_status("in progress")
    else:
        chapters = soup.select_one("dd.chapters")
        if chapters and "/" in chapters.get_text():
            parts = chapters.get_text(strip=True).split("/")
            if len(parts) == 2 and parts[0] == parts[1]:
                status = parse_work_status("completed")
            else:
                status = parse_work_status("in progress")

    words = None
    words_el = soup.select_one("dd.words")
    if words_el:
        digits = re.sub(r"[^\d]", "", words_el.get_text())
        words = int(digits) if digits else None

    fandom_els = soup.select("dd.fandom a")
    fandom = ", ".join(a.get_text(strip=True) for a in fandom_els) or None

    pairing_els = soup.select("dd.relationship a")
    pairing = ", ".join(a.get_text(strip=True) for a in pairing_els[:3]) or None

    tag_els = soup.select("dd.freeform a") + soup.select("dd.additional a")
    tags = [a.get_text(strip=True) for a in tag_els[:20]]

    logger.info("AO3 разобран: «%s» автор=%s", title, author)
    return ParsedWork(
        title=title,
        author=author,
        status=status,
        word_count=words,
        tags=tags,
        fandom=fandom,
        pairing=pairing,
        source_url=url,
        platform="ao3",
    )
