"""Сопоставление найденных названий с общей базой."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from rapidfuzz import fuzz
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Work, WorkAlias
from app.services.normalize import normalize_text

logger = logging.getLogger(__name__)

# Пороги уверенности сопоставления
HIGH_SCORE = 92
MEDIUM_SCORE = 78


@dataclass
class MatchCandidate:
    """Кандидат на совпадение с работой из базы."""

    work: Work
    score: float
    reason: str


@dataclass
class MatchResult:
    """Результат попытки сопоставить извлечённое название."""

    query_title: str
    query_author: str | None
    confidence: str  # high | medium | low | none
    best: MatchCandidate | None
    candidates: list[MatchCandidate]


async def find_matches(
    session: AsyncSession,
    title: str,
    author: str | None = None,
    limit: int = 5,
) -> MatchResult:
    """Ищет кандидатов в общей базе по названию и (опционально) автору."""
    norm_title = normalize_text(title)
    norm_author = normalize_text(author) if author else ""

    if not norm_title:
        return MatchResult(
            query_title=title,
            query_author=author,
            confidence="none",
            best=None,
            candidates=[],
        )

    # Точное совпадение по alias
    alias_result = await session.execute(
        select(WorkAlias)
        .options(selectinload(WorkAlias.work))
        .where(WorkAlias.normalized_alias == norm_title)
        .limit(10)
    )
    aliases = alias_result.scalars().all()
    if aliases:
        candidates: list[MatchCandidate] = []
        for alias in aliases:
            score = 100.0
            if norm_author and alias.work.normalized_author:
                if alias.work.normalized_author == norm_author:
                    score = 100.0
                else:
                    score = 90.0
            candidates.append(
                MatchCandidate(work=alias.work, score=score, reason="exact_alias")
            )
        candidates.sort(key=lambda c: c.score, reverse=True)
        best = candidates[0]
        confidence = "high" if best.score >= HIGH_SCORE else "medium"
        logger.info(
            "Точное совпадение alias «%s» → work_id=%s score=%.1f",
            title,
            best.work.id,
            best.score,
        )
        return MatchResult(title, author, confidence, best, candidates[:limit])

    # Точное совпадение по нормализованному названию
    title_result = await session.execute(
        select(Work)
        .options(selectinload(Work.aliases))
        .where(Work.normalized_title == norm_title)
        .limit(10)
    )
    exact_works = title_result.scalars().all()
    if exact_works:
        candidates = []
        for work in exact_works:
            score = 98.0
            if norm_author and work.normalized_author:
                if work.normalized_author == norm_author:
                    score = 100.0
                else:
                    score = 88.0
            candidates.append(MatchCandidate(work=work, score=score, reason="exact_title"))
        candidates.sort(key=lambda c: c.score, reverse=True)
        best = candidates[0]
        confidence = "high" if best.score >= HIGH_SCORE else "medium"
        return MatchResult(title, author, confidence, best, candidates[:limit])

    # Нечёткий поиск: подгружаем ограниченный набор кандидатов
    like = f"%{norm_title[:40]}%"
    fuzzy_result = await session.execute(
        select(Work)
        .options(selectinload(Work.aliases))
        .where(
            or_(
                Work.normalized_title.ilike(like),
                Work.title.ilike(f"%{title[:40]}%"),
            )
        )
        .limit(50)
    )
    works = list(fuzzy_result.scalars().all())

    # Также ищем по aliases через ILIKE
    alias_like = await session.execute(
        select(WorkAlias)
        .options(selectinload(WorkAlias.work).selectinload(Work.aliases))
        .where(WorkAlias.normalized_alias.ilike(like))
        .limit(50)
    )
    for alias in alias_like.scalars().all():
        if alias.work not in works:
            works.append(alias.work)

    # Если мало кандидатов — берём срез всей базы для fuzzy (для стартовой базы 50–150 ок)
    if len(works) < 10:
        all_result = await session.execute(
            select(Work).options(selectinload(Work.aliases)).limit(300)
        )
        works = list(all_result.scalars().all())

    scored: list[MatchCandidate] = []
    for work in works:
        title_score = fuzz.token_set_ratio(norm_title, work.normalized_title)
        best_alias_score = 0.0
        for alias in work.aliases:
            best_alias_score = max(
                best_alias_score,
                fuzz.token_set_ratio(norm_title, alias.normalized_alias),
            )
        score = max(title_score, best_alias_score)
        if norm_author and work.normalized_author:
            author_score = fuzz.token_set_ratio(norm_author, work.normalized_author)
            if author_score >= 85:
                score = min(100.0, score + 8)
            elif author_score < 50 and score < 95:
                score = max(0.0, score - 10)
        if score >= MEDIUM_SCORE - 5:
            scored.append(
                MatchCandidate(
                    work=work,
                    score=float(score),
                    reason="fuzzy",
                )
            )

    scored.sort(key=lambda c: c.score, reverse=True)
    top = scored[:limit]
    best = top[0] if top else None

    if best and best.score >= HIGH_SCORE:
        confidence = "high"
    elif best and best.score >= MEDIUM_SCORE:
        confidence = "medium"
    elif best:
        confidence = "low"
    else:
        confidence = "none"

    logger.info(
        "Нечёткий матчинг «%s»: confidence=%s best_score=%s",
        title,
        confidence,
        f"{best.score:.1f}" if best else "—",
    )
    return MatchResult(title, author, confidence, best, top)
