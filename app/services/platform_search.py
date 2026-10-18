"""Поиск работ на AO3 / Ficbook для обогащения метаданных после extract."""

from __future__ import annotations

import asyncio
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
# Уверенный единственный кандидат — достаточно для авто-парсинга страницы
AUTO_PARSE_SCORE = 88
MEDIUM_SCORE = 78
_MAX_SEARCHES_PER_BATCH_DEFAULT = 15
# Поиск не должен съедать весь ARQ job_timeout после долгого скачивания рилса
_SEARCH_HTTP_TIMEOUT = 14.0
_SEARCH_HTTP_ATTEMPTS = 2
_ENRICH_PER_TITLE_TIMEOUT = 45.0
_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")
_EMPTY_RESULT_RE = re.compile(
    r"ничего\s+не\s+найдено|по\s+вашему\s+запросу\s+ничего|нет\s+результатов",
    re.I,
)
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _max_searches_per_batch() -> int:
    from app.config import get_settings

    try:
        return max(1, int(get_settings().platform_search_budget))
    except Exception:
        return _MAX_SEARCHES_PER_BATCH_DEFAULT


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

    # Алиасы вида «Скованные/Manacled» или «A | B»
    aliases = [
        normalize_text(p)
        for p in re.split(r"[/|]", hit.title or "")
        if p and p.strip()
    ]
    if norm_h not in aliases:
        aliases.append(norm_h)

    if norm_q in aliases or norm_q == norm_h:
        score = 100.0
    else:
        # WRatio менее щедр, чем token_set, к коротким запросам в длинных названиях
        score = float(fuzz.WRatio(norm_q, norm_h))
        for alias in aliases:
            if alias:
                score = max(score, float(fuzz.ratio(norm_q, alias)))
        # «Омут» ⊂ «Омут потерянной вечности» — не auto-high
        if score >= HIGH_SCORE and len(norm_h) > len(norm_q) + max(6, len(norm_q) // 2):
            score = min(score, float(MEDIUM_SCORE + 5))

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


def _ficbook_page_is_empty(html: str) -> bool:
    """True, если в выдаче явно «ничего не найдено» (premium-баннеры не считаем)."""
    return bool(_EMPTY_RESULT_RE.search(html or ""))


def parse_ficbook_search_html(html: str) -> list[SearchHit]:
    """Парсит страницу результатов поиска Ficbook."""
    if _ficbook_page_is_empty(html):
        return []

    soup = BeautifulSoup(html, "lxml")
    hits: list[SearchHit] = []
    seen: set[str] = set()

    # Приоритет: заголовки карточек, не рекламные visit-link
    anchors = list(soup.select("h3.fanfic-inline-title a.visit-link[href*='/readfic/']"))
    if not anchors:
        anchors = list(soup.select("a.visit-link[href*='/readfic/'], a[href*='/readfic/']"))

    for a in anchors:
        href = a.get("href") or ""
        # Реклама / премиум-промо в «пустой» выдаче
        if "source=premium" in href or "from_promo=" in href or "premiumVisit=" in href:
            continue
        m = re.search(r"(/readfic/[\w-]+)", href)
        if not m:
            continue
        path = m.group(1)
        if path in seen:
            continue
        title = a.get_text(" ", strip=True)
        if not title or len(title) < 2:
            continue
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
    """Актуальный поиск Ficbook (старый /search/fanfiction отдаёт 404)."""
    q = title if not author else f"{title} {author}"
    return f"https://ficbook.net/search_fanfics?query={quote_plus(q)}"


def ficbook_find_url(title: str, author: str | None = None) -> str:
    """Расширенный поиск по названию (/find часто 403 для ботов — fallback)."""
    params: dict[str, str] = {"title": title}
    if author:
        params["author1"] = author
    return "https://ficbook.net/find?" + urlencode(params)


async def _fetch_search_html(url: str) -> str:
    """Быстрый fetch для поиска: короткий timeout, browser UA."""
    return await fetch_html(
        url,
        timeout=_SEARCH_HTTP_TIMEOUT,
        attempts=_SEARCH_HTTP_ATTEMPTS,
        user_agent=_BROWSER_UA,
        referer="https://ficbook.net/" if "ficbook.net" in url else None,
    )


async def _search_ficbook(title: str, author: str | None) -> list[SearchHit]:
    """search_fanfics; при пустой выдаче — попытка /find (может быть 403)."""
    url = ficbook_search_url(title, author)
    html = await _fetch_search_html(url)
    hits = parse_ficbook_search_html(html)
    if hits:
        return hits
    # Fallback: точный поиск по title (часто закрыт antifraud)
    try:
        find_html = await _fetch_search_html(ficbook_find_url(title, author))
        if "Проверка безопасности" in find_html or "Just a moment" in find_html:
            logger.info("Ficbook /find заблокирован antifraud для «%s»", title)
            return []
        return parse_ficbook_search_html(find_html)
    except Exception as exc:
        logger.info("Ficbook /find недоступен для «%s»: %s", title, exc)
        return []


async def _search_one_platform(
    platform: str, title: str, author: str | None
) -> list[SearchHit]:
    """Один HTTP-поиск + parse HTML (ошибки глотаем — enrich не обязателен)."""
    try:
        if platform == "ao3":
            url = ao3_search_url(title, author)
            html = await _fetch_search_html(url)
            hits = parse_ao3_search_html(html)
        else:
            hits = await _search_ficbook(title, author)
        logger.info(
            "Поиск %s «%s»: кандидатов=%s",
            platform,
            title,
            len(hits),
        )
        return hits
    except Exception as exc:
        err = str(exc)
        hint = ""
        lower = err.lower()
        if "403" in lower or "forbidden" in lower:
            hint = " (возможен блок площадки / нужен SOCKS5_PROXY)"
        elif "404" in lower or "not found" in lower:
            hint = " (URL поиска устарел?)"
        elif "timeout" in lower or "timed out" in lower:
            hint = " (таймаут)"
        logger.warning(
            "Поиск %s для «%s» не удался%s: %s",
            platform,
            title,
            hint,
            exc,
        )
        return []


def _should_auto_parse(ranked: list[SearchHit]) -> bool:
    """Единственный уверенный хит — парсим страницу без вопроса пользователю."""
    if not ranked:
        return False
    best = ranked[0]
    if best.score >= HIGH_SCORE:
        high_peers = [h for h in ranked if h.score >= HIGH_SCORE]
        return len(high_peers) == 1
    if best.score >= AUTO_PARSE_SCORE:
        second = ranked[1].score if len(ranked) > 1 else 0.0
        return (best.score - second) >= 8.0
    return False


async def _enrich_from_platforms_inner(
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
    if _should_auto_parse(ranked):
        try:
            parsed = await parse_fic_url(best.url)
        except Exception:
            logger.exception("Не удалось разобрать страницу %s", best.url)
            return EnrichmentResult(confidence="medium", hits=ranked[:5])
        if not parsed:
            return EnrichmentResult(confidence="medium", hits=ranked[:5])
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
                return EnrichmentResult(confidence="medium", hits=ranked[:5], parsed=parsed)
        return EnrichmentResult(confidence="high", parsed=parsed, hits=ranked[:5])

    if best.score >= MEDIUM_SCORE:
        return EnrichmentResult(confidence="medium", hits=ranked[:5])
    return EnrichmentResult(confidence="none", hits=ranked[:5])


async def enrich_from_platforms(
    title: str,
    author: str | None = None,
) -> EnrichmentResult:
    """Обёртка с жёстким deadline — не валит весь video_url job."""
    try:
        return await asyncio.wait_for(
            _enrich_from_platforms_inner(title, author),
            timeout=_ENRICH_PER_TITLE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "Enrich timeout (%.0f с) для «%s» — продолжаем без досье",
            _ENRICH_PER_TITLE_TIMEOUT,
            title,
        )
        return EnrichmentResult(confidence="none", hits=[])
    except Exception:
        logger.exception("Enrich упал для «%s» — продолжаем без досье", title)
        return EnrichmentResult(confidence="none", hits=[])


class SearchBudget:
    """Лимит внешних поисков на один batch resolve."""

    def __init__(self, limit: int | None = None) -> None:
        self.remaining = limit if limit is not None else _max_searches_per_batch()

    def take(self) -> bool:
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True
