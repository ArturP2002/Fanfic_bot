"""Поиск работ на AO3 / Ficbook для обогащения метаданных после extract."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import quote_plus, urlencode

from bs4 import BeautifulSoup
from rapidfuzz import fuzz

from app.services.normalize import normalize_text
from app.services.parsers.base import ParsedWork, fetch_html, parse_fic_url

logger = logging.getLogger(__name__)

HIGH_SCORE = 92
MEDIUM_SCORE = 78
_MAX_SEARCHES_PER_BATCH = 5
_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")


@dataclass
class SearchHit:
    """Кандидат из выдачи поиска площадки."""

    title: str
    author: str | None
    url: str
    platform: str  # ao3 | ficbook
    score: float = 0.0


@dataclass
class EnrichmentResult:
    """Итог поиска+парсинга страницы (или отказ)."""

    confidence: str  # high | medium | none
    parsed: ParsedWork | None = None
    hits: list[SearchHit] | None = None


def _has_cyrillic(text: str) -> bool:
    return bool(_CYRILLIC_RE.search(text or ""))


def platform_order(title: str) -> tuple[str, ...]:
    """Кириллица → Ficbook first, иначе AO3 first."""
    if _has_cyrillic(title):
        return ("ficbook", "ao3")
    return ("ao3", "ficbook")


def score_hit(query_title: str, query_author: str | None, hit: SearchHit) -> float:
    """Оценка кандидата к запросу (0–100)."""
    norm_q = normalize_text(query_title)
    norm_h = normalize_text(hit.title)
    if not norm_q or not norm_h:
        return 0.0
    score = float(fuzz.token_set_ratio(norm_q, norm_h))
    if query_author and hit.author:
        author_score = float(
            fuzz.token_set_ratio(normalize_text(query_author), normalize_text(hit.author))
        )
        if author_score >= 85:
            score = min(100.0, score + 8)
        elif author_score < 50 and score < 95:
            score = max(0.0, score - 10)
    return score


def rank_hits(
    query_title: str, query_author: str | None, hits: list[SearchHit]
) -> list[SearchHit]:
    """Сортирует кандидатов по score убыв."""
    ranked: list[SearchHit] = []
    for hit in hits:
        hit.score = score_hit(query_title, query_author, hit)
        if hit.score >= MEDIUM_SCORE - 5:
            ranked.append(hit)
    ranked.sort(key=lambda h: h.score, reverse=True)
    return ranked


def parse_ao3_search_html(html: str) -> list[SearchHit]:
    """Парсит страницу результатов AO3 works search."""
    soup = BeautifulSoup(html, "lxml")
    hits: list[SearchHit] = []
    for li in soup.select("li.work.blurb, li.blurb.group"):
        heading = li.select_one("h4.heading")
        if not heading:
            continue
        title_a = heading.select_one("a[href*='/works/']")
        if not title_a:
            continue
        href = title_a.get("href") or ""
        if not href.startswith("http"):
            href = "https://archiveofourown.org" + href
        # убираем /chapters/...
        m = re.search(r"(https?://[^/]+/works/\d+)", href)
        if m:
            href = m.group(1)
        author_a = heading.select_one("a[rel='author']")
        author = author_a.get_text(strip=True) if author_a else None
        title = title_a.get_text(strip=True)
        if title and href:
            hits.append(
                SearchHit(title=title, author=author, url=href, platform="ao3")
            )
    return hits


def parse_ficbook_search_html(html: str) -> list[SearchHit]:
    """Парсит страницу результатов поиска Ficbook."""
    soup = BeautifulSoup(html, "lxml")
    hits: list[SearchHit] = []
    # Карточки: a с /readfic/
    seen: set[str] = set()
    for a in soup.select("a[href*='/readfic/']"):
        href = a.get("href") or ""
        m = re.search(r"(/readfic/[\w-]+)", href)
        if not m:
            continue
        path = m.group(1)
        if path in seen:
            continue
        title = a.get_text(" ", strip=True)
        if not title or len(title) < 2:
            continue
        # пропускаем служебные ссылки вроде «Читать»
        if title.lower() in {"читать", "целиком", "по главам"}:
            continue
        seen.add(path)
        url = "https://ficbook.net" + path
        author = None
        parent = a.find_parent(["article", "div", "li"])
        if parent:
            author_a = parent.select_one(
                "a[href*='/authors/'], a.author, span.author a"
            )
            if author_a:
                author = author_a.get_text(strip=True) or None
        hits.append(
            SearchHit(title=title, author=author, url=url, platform="ficbook")
        )
        if len(hits) >= 15:
            break
    return hits


def ao3_search_url(title: str, author: str | None = None) -> str:
    params = {
        "work_search[title]": title,
        "work_search[complete]": "",
        "commit": "Search",
    }
    if author:
        params["work_search[creators]"] = author
    return "https://archiveofourown.org/works?" + urlencode(params)


def ficbook_search_url(title: str, author: str | None = None) -> str:
    # find по названию; автор — доп. параметр, если площадка примет
    q = title
    if author:
        q = f"{title} {author}"
    return f"https://ficbook.net/find?title={quote_plus(title)}&q={quote_plus(q)}"


async def _search_one_platform(
    platform: str, title: str, author: str | None
) -> list[SearchHit]:
    """Один HTTP-поиск + parse HTML."""
    try:
        if platform == "ao3":
            url = ao3_search_url(title, author)
            html = await fetch_html(url)
            hits = parse_ao3_search_html(html)
        else:
            url = ficbook_search_url(title, author)
            html = await fetch_html(url)
            hits = parse_ficbook_search_html(html)
        logger.info(
            "Поиск %s «%s»: кандидатов=%s",
            platform,
            title,
            len(hits),
        )
        return hits
    except Exception:
        logger.exception("Ошибка поиска %s для «%s»", platform, title)
        return []


async def enrich_from_platforms(
    title: str,
    author: str | None = None,
) -> EnrichmentResult:
    """Ищет на AO3/Ficbook и при high-match парсит страницу работы."""
    all_hits: list[SearchHit] = []
    for platform in platform_order(title):
        hits = await _search_one_platform(platform, title, author)
        all_hits.extend(hits)
        ranked = rank_hits(title, author, hits)
        if ranked and ranked[0].score >= HIGH_SCORE:
            # достаточно одного уверенного с этой площадки
            all_hits = ranked
            break

    ranked = rank_hits(title, author, all_hits)
    if not ranked:
        return EnrichmentResult(confidence="none", hits=[])

    best = ranked[0]
    high_peers = [h for h in ranked if h.score >= HIGH_SCORE]
    if len(high_peers) == 1 and best.score >= HIGH_SCORE:
        try:
            parsed = await parse_fic_url(best.url)
        except Exception:
            logger.exception("Не удалось разобрать страницу %s", best.url)
            return EnrichmentResult(confidence="medium", hits=ranked[:3])
        if not parsed:
            return EnrichmentResult(confidence="medium", hits=ranked[:3])
        # если автор запроса есть и сильно расходится — понижаем
        if author and parsed.author:
            author_score = fuzz.token_set_ratio(
                normalize_text(author), normalize_text(parsed.author)
            )
            if author_score < 50:
                logger.info(
                    "High title, но автор не сходится (%s vs %s) — medium",
                    author,
                    parsed.author,
                )
                return EnrichmentResult(confidence="medium", hits=ranked[:3], parsed=parsed)
        return EnrichmentResult(confidence="high", parsed=parsed, hits=ranked[:3])

    if best.score >= MEDIUM_SCORE:
        return EnrichmentResult(confidence="medium", hits=ranked[:3])
    return EnrichmentResult(confidence="none", hits=ranked[:3])


class SearchBudget:
    """Лимит внешних поисков на один batch resolve."""

    def __init__(self, limit: int = _MAX_SEARCHES_PER_BATCH) -> None:
        self.remaining = limit

    def take(self) -> bool:
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True
