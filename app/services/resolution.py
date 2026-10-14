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


async def resolve_extracted(
    session: AsyncSession,
    user_id: int,
    extracted: list[ExtractedWork],
    *,
    auto_create_low_confidence: bool = False,
) -> ResolutionBatch:
    """Сопоставляет извлечённые названия с базой и библиотекой пользователя."""
    batch = ResolutionBatch()
    seen_work_ids: set[int] = set()

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
            # Новое для каталога — только валидный title и confidence >= 0.7
            # Обрывки речи не пишем в works даже с needs_review
            title_ok = _is_valid_title(item.title)
            can_create = (
                auto_create_low_confidence
                or (title_ok and item.confidence >= _AUTO_CREATE_CONFIDENCE)
            )
            if can_create and title_ok:
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
                )
                seen_work_ids.add(work.id)
            else:
                # Пункт в результате без work — нужно подтверждение / пропуск
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
