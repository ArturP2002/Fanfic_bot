"""Резолюция извлечённых произведений: матчинг, создание, сводка для UI."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LibraryItem, Work
from app.models.enums import ConfirmationStatus, ReadingStatus
from app.services import library as library_service
from app.services.extract import ExtractedWork, _is_valid_title
from app.services.matching import find_matches
from app.services.normalize import normalize_text
from app.services.platform_search import SearchBudget, SearchHit, enrich_from_platforms
from app.services.works import (
    create_work,
    enrich_work,
    find_work_by_url,
    split_translation_urls,
    volume_from_words,
)

# Автосоздание в каталог только при достаточной уверенности extract
_AUTO_CREATE_CONFIDENCE = 0.7

logger = logging.getLogger(__name__)


def _dedupe_extracted(items: list[ExtractedWork]) -> list[ExtractedWork]:
    """Убирает повторы одного названия в пакете (OCR+speech часто дублируют)."""
    seen: set[str] = set()
    out: list[ExtractedWork] = []
    for item in items:
        key = normalize_text(item.title)
        if not key or key in seen:
            if key in seen:
                logger.info("Пропуск дубля extract «%s»", item.title)
            continue
        seen.add(key)
        out.append(item)
    return out


@dataclass
class ResolvedItem:
    """Одно произведение после сопоставления с базой и библиотекой."""

    extracted: ExtractedWork
    work: Work | None = None
    library_item: LibraryItem | None = None
    match_confidence: str = "none"  # high|medium|low|none
    needs_user_choice: bool = False
    candidate_work_ids: list[int] = field(default_factory=list)
    is_new_for_catalog: bool = False
    needs_catalog_confirm: bool = False
    # Кандидаты с AO3/Ficbook (medium), для UI выбора
    platform_hits: list[SearchHit] = field(default_factory=list)

    @property
    def is_new_for_user(self) -> bool:
        return self.library_item is None and self.work is not None

    @property
    def reading_status(self) -> str | None:
        return self.library_item.reading_status if self.library_item else None


@dataclass
class ResolutionBatch:
    """Пакет результатов для показа пользователю."""

    items: list[ResolvedItem] = field(default_factory=list)

    @property
    def new_items(self) -> list[ResolvedItem]:
        return [i for i in self.items if i.is_new_for_user and i.work and not i.needs_user_choice]

    @property
    def want_items(self) -> list[ResolvedItem]:
        return [
            i
            for i in self.items
            if i.reading_status == ReadingStatus.WANT.value
        ]

    @property
    def read_items(self) -> list[ResolvedItem]:
        return [
            i
            for i in self.items
            if i.reading_status == ReadingStatus.READ.value
        ]

    @property
    def other_known(self) -> list[ResolvedItem]:
        return [
            i
            for i in self.items
            if i.library_item
            and i.reading_status
            not in {ReadingStatus.WANT.value, ReadingStatus.READ.value}
        ]


async def _create_from_ocr_dossier(
    session: AsyncSession,
    item: ExtractedWork,
) -> Work:
    """Создаёт работу из карточки Ficbook/AO3, уже прочитанной в OCR."""
    author_ok = bool(item.author)
    work = await create_work(
        session,
        title=item.title,
        author=item.author,
        fandom=item.fandom,
        pairing=item.pairing,
        status=item.status or "unknown",
        word_count=item.word_count,
        volume_label=volume_from_words(item.word_count),
        tags=item.tags,
        confirmation_status=(
            ConfirmationStatus.CONFIRMED.value
            if author_ok and item.has_dossier_meta
            else ConfirmationStatus.NEEDS_REVIEW.value
        ),
    )
    logger.info(
        "OCR-досье: новая work_id=%s «%s» words=%s fandom=%s",
        work.id,
        work.title,
        item.word_count,
        item.fandom,
    )
    return work


async def _create_from_platform_enrichment(
    session: AsyncSession,
    item: ExtractedWork,
    *,
    search_budget: SearchBudget,
) -> tuple[Work | None, str, bool, list[SearchHit]]:
    """Пробует AO3/Ficbook.

    Returns:
        (work|None, match_confidence, needs_confirm, platform_hits)
    """
    # Карточка уже в OCR — не тратим бюджет поиска и не ждём пустую выдачу
    if item.from_ocr_dossier and item.has_dossier_meta:
        work = await _create_from_ocr_dossier(session, item)
        needs_confirm = (
            work.confirmation_status == ConfirmationStatus.NEEDS_REVIEW.value
        )
        return work, "high", needs_confirm, []

    if not search_budget.take():
        logger.info("Лимит внешних поисков исчерпан — без enrich для «%s»", item.title)
        return None, "none", True, []

    try:
        result = await enrich_from_platforms(item.title, item.author)
    except Exception:
        logger.exception(
            "Enrich неожиданно упал для «%s» — stub без досье",
            item.title,
        )
        return None, "none", True, []

    hits = list(result.hits or [])

    if result.confidence == "high" and result.parsed:
        parsed = result.parsed
        existing = None
        if parsed.source_url:
            existing = await find_work_by_url(session, parsed.source_url)
        if existing:
            await enrich_work(
                session,
                existing,
                fandom=parsed.fandom or item.fandom,
                pairing=parsed.pairing or item.pairing,
                status=parsed.status or item.status,
                word_count=parsed.word_count or item.word_count,
                tags=parsed.tags or item.tags,
                source_url=parsed.source_url,
                extra_titles=[item.title, parsed.title],
            )
            logger.info(
                "Enrich high: существующая work_id=%s «%s»",
                existing.id,
                existing.title,
            )
            return existing, "high", False, hits

        author_ok = bool(parsed.author) and (
            not item.author
            or normalize_authors_close(item.author, parsed.author)
        )
        work = await create_work(
            session,
            title=parsed.title or item.title,
            author=parsed.author or item.author,
            fandom=parsed.fandom or item.fandom,
            pairing=parsed.pairing or item.pairing,
            status=parsed.status or item.status or "unknown",
            word_count=parsed.word_count or item.word_count,
            volume_label=volume_from_words(parsed.word_count or item.word_count),
            tags=parsed.tags or item.tags,
            source_url=parsed.source_url,
            confirmation_status=(
                ConfirmationStatus.CONFIRMED.value
                if author_ok
                else ConfirmationStatus.NEEDS_REVIEW.value
            ),
            other_titles=[item.title] if item.title != parsed.title else None,
        )
        needs_confirm = (
            work.confirmation_status == ConfirmationStatus.NEEDS_REVIEW.value
        )
        logger.info(
            "Enrich high: новая work_id=%s «%s» platform=%s confirm=%s",
            work.id,
            work.title,
            parsed.platform,
            needs_confirm,
        )
        return work, "high", needs_confirm, hits

    # Поиск не нашёл URL, но OCR уже дал досье — создаём с метаданными
    if item.has_dossier_meta:
        work = await _create_from_ocr_dossier(session, item)
        needs_confirm = (
            work.confirmation_status == ConfirmationStatus.NEEDS_REVIEW.value
        )
        return work, "high", needs_confirm, hits[:5]

    if result.confidence == "medium" and hits:
        logger.info(
            "Enrich medium для «%s»: hits=%s — предлагаем выбор пользователю",
            item.title,
            len(hits),
        )
        return None, "medium", True, hits[:5]

    if hits:
        logger.info(
            "Enrich none/low для «%s», но есть hits=%s — предлагаем выбор",
            item.title,
            len(hits),
        )
        return None, "none", True, hits[:5]

    return None, "none", True, []


def normalize_authors_close(a: str, b: str) -> bool:
    """Грубая проверка совпадения авторов без циклического импорта matching."""
    from rapidfuzz import fuzz

    from app.services.normalize import normalize_text

    return fuzz.token_set_ratio(normalize_text(a), normalize_text(b)) >= 85


async def resolve_extracted(
    session: AsyncSession,
    user_id: int,
    extracted: list[ExtractedWork],
    *,
    auto_create_low_confidence: bool = False,
    platform_enrich: bool = True,
) -> ResolutionBatch:
    """Сопоставляет извлечённые названия с базой и библиотекой пользователя."""
    batch = ResolutionBatch()
    seen_work_ids: set[int] = set()
    search_budget = SearchBudget()
    extracted = _dedupe_extracted(extracted)

    for item in extracted:
        match = await find_matches(session, item.title, item.author)
        resolved = ResolvedItem(extracted=item, match_confidence=match.confidence)

        if match.confidence == "high" and match.best:
            work = match.best.work
            if work.id in seen_work_ids:
                logger.info("Пропуск дубля в пакете work_id=%s", work.id)
                continue
            seen_work_ids.add(work.id)
            resolved.work = work
            resolved.library_item = await library_service.get_library_item(
                session, user_id, work.id
            )
        elif match.confidence == "medium" and match.candidates:
            resolved.needs_user_choice = True
            resolved.candidate_work_ids = [c.work.id for c in match.candidates[:3]]
            if match.best:
                resolved.work = match.best.work
        else:
            # Новое для каталога — сначала platform enrich, потом stub
            title_ok = _is_valid_title(item.title)
            can_create = (
                auto_create_low_confidence
                or (title_ok and item.confidence >= _AUTO_CREATE_CONFIDENCE)
            )
            enriched_work = None
            enrich_conf = "none"
            needs_confirm = True
            platform_hits: list[SearchHit] = []
            if platform_enrich and can_create and title_ok:
                (
                    enriched_work,
                    enrich_conf,
                    needs_confirm,
                    platform_hits,
                ) = await _create_from_platform_enrichment(
                    session, item, search_budget=search_budget
                )

            if enriched_work:
                if enriched_work.id in seen_work_ids:
                    logger.info("Пропуск дубля после enrich work_id=%s", enriched_work.id)
                    continue
                seen_work_ids.add(enriched_work.id)
                resolved.work = enriched_work
                resolved.match_confidence = enrich_conf
                resolved.is_new_for_catalog = True
                resolved.needs_catalog_confirm = needs_confirm
                resolved.library_item = await library_service.get_library_item(
                    session, user_id, enriched_work.id
                )
            elif platform_hits:
                # Не создаём пустой stub — сразу UI выбора с площадки
                resolved.match_confidence = enrich_conf
                resolved.platform_hits = platform_hits
                resolved.needs_user_choice = True
                resolved.needs_catalog_confirm = True
                logger.info(
                    "Platform hits для «%s»: %s вариантов без stub",
                    item.title,
                    len(platform_hits),
                )
            elif can_create and title_ok:
                work = await create_work(
                    session,
                    title=item.title,
                    author=item.author,
                    confirmation_status=(
                        ConfirmationStatus.CONFIRMED.value
                        if item.confidence >= 0.8 and item.author
                        else ConfirmationStatus.NEEDS_REVIEW.value
                    ),
                )
                resolved.work = work
                resolved.is_new_for_catalog = True
                resolved.needs_catalog_confirm = (
                    work.confirmation_status == ConfirmationStatus.NEEDS_REVIEW.value
                    or enrich_conf == "medium"
                )
                seen_work_ids.add(work.id)
            else:
                resolved.needs_catalog_confirm = True
                logger.info(
                    "Извлечение без записи в каталог: «%s» conf=%.2f valid=%s",
                    item.title,
                    item.confidence,
                    title_ok,
                )

        batch.items.append(resolved)

    logger.info(
        "Резолюция: всего=%s новых_для_пользователя=%s",
        len(batch.items),
        len(batch.new_items),
    )
    return batch


async def resolve_parsed_link(
    session: AsyncSession,
    user_id: int,
    *,
    title: str,
    author: str | None,
    status: str | None = None,
    word_count: int | None = None,
    tags: list[str] | None = None,
    fandom: str | None = None,
    pairing: str | None = None,
    source_url: str | None = None,
    original_url: str | None = None,
) -> ResolvedItem:
    """Резолюция произведения, полученного по прямой ссылке."""
    extracted = ExtractedWork(title=title, author=author, confidence=0.95)
    main_url, translation_url = split_translation_urls(source_url, original_url)

    work = None
    for url in (source_url, original_url):
        if url and not work:
            work = await find_work_by_url(session, url)
    if work:
        resolved = ResolvedItem(extracted=extracted, match_confidence="high")
    else:
        match = await find_matches(session, title, author)
        resolved = ResolvedItem(extracted=extracted, match_confidence=match.confidence)
        if match.confidence in {"high", "medium"} and match.best and match.best.score >= 78:
            if match.confidence == "medium" and len(match.candidates) > 1:
                resolved.needs_user_choice = True
                resolved.candidate_work_ids = [c.work.id for c in match.candidates[:3]]
            work = match.best.work

    if work:
        await enrich_work(
            session,
            work,
            fandom=fandom,
            pairing=pairing,
            status=status,
            word_count=word_count,
            tags=tags,
            source_url=main_url,
            translation_url=translation_url,
            # Название по ссылке закрепляем как alias только при уверенном совпадении
            extra_titles=[title] if resolved.match_confidence == "high" else None,
        )
        resolved.work = work
        resolved.library_item = await library_service.get_library_item(
            session, user_id, work.id
        )
        return resolved

    work = await create_work(
        session,
        title=title,
        author=author,
        fandom=fandom,
        pairing=pairing,
        status=status or "unknown",
        word_count=word_count,
        volume_label=volume_from_words(word_count),
        tags=tags,
        source_url=main_url,
        translation_url=translation_url,
        confirmation_status=ConfirmationStatus.CONFIRMED.value,
    )
    resolved.work = work
    resolved.is_new_for_catalog = True
    return resolved
