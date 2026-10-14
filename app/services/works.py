"""Операции с общей базой произведений."""

from __future__ import annotations

import logging

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Work, WorkAlias
from app.models.enums import ConfirmationStatus, WorkStatus
from app.services.normalize import normalize_text, split_aliases, split_bilingual_title
from app.services.parsers.base import canonical_url, detect_platform

logger = logging.getLogger(__name__)


def is_placeholder_url(url: str | None) -> bool:
    """Проверяет, что ссылка — тестовая заглушка, а не реальная страница."""
    if not url:
        return True
    lowered = url.lower()
    return any(
        marker in lowered
        for marker in ("/works/example", "example.com", "placeholder", "your-link")
    )


def split_translation_urls(
    page_url: str | None, original_url: str | None
) -> tuple[str | None, str | None]:
    """(ссылка, ссылка на перевод): у переводов источник — оригинал на площадке."""
    if page_url and original_url and detect_platform(original_url):
        return canonical_url(original_url), page_url
    return page_url, None


async def find_work_by_url(session: AsyncSession, url: str) -> Work | None:
    """Ищет работу по ссылке на оригинал или перевод."""
    canonical = canonical_url(url)
    variants = {canonical, canonical + "/", url.strip()}
    result = await session.execute(
        select(Work).where(
            or_(Work.source_url.in_(variants), Work.translation_url.in_(variants))
        )
    )
    return result.scalars().first()


async def enrich_work(
    session: AsyncSession,
    work: Work,
    *,
    fandom: str | None = None,
    pairing: str | None = None,
    status: str | None = None,
    word_count: int | None = None,
    tags: list[str] | None = None,
    source_url: str | None = None,
    translation_url: str | None = None,
    original_title: str | None = None,
    russian_title: str | None = None,
    extra_titles: list[str] | None = None,
) -> bool:
    """Дополняет пустые поля существующей работы и добавляет новые названия.

    Заполненное не перезаписывается; заглушка ссылки из шаблона заменяется.
    Возвращает True, если что-то изменилось.
    """
    changed = False
    if source_url and is_placeholder_url(work.source_url):
        work.source_url = source_url
        changed = True
    if translation_url and not work.translation_url and translation_url != work.source_url:
        work.translation_url = translation_url
        changed = True
    if word_count and not work.word_count:
        work.word_count = word_count
        work.volume_label = volume_from_words(word_count)
        changed = True
    if tags and not work.tags:
        work.tags = tags
        changed = True
    if fandom and not work.fandom:
        work.fandom = fandom
        changed = True
    if pairing and not work.pairing:
        work.pairing = pairing
        changed = True
    if status and status != WorkStatus.UNKNOWN.value and (
        not work.status or work.status == WorkStatus.UNKNOWN.value
    ):
        work.status = status
        changed = True
    if original_title and not work.original_title:
        work.original_title = original_title
        changed = True
    if russian_title and not work.russian_title:
        work.russian_title = russian_title
        changed = True

    names = [n for n in (original_title, russian_title, *(extra_titles or [])) if n]
    if names:
        existing = await session.execute(
            select(WorkAlias.normalized_alias).where(WorkAlias.work_id == work.id)
        )
        seen = set(existing.scalars().all()) | {work.normalized_title}
        for name in names:
            norm = normalize_text(name)
            if norm and norm not in seen:
                seen.add(norm)
                session.add(
                    WorkAlias(
                        work_id=work.id,
                        alias=name.strip(),
                        normalized_alias=norm,
                        alias_type="alt",
                    )
                )
                changed = True

    if changed:
        await session.commit()
        logger.info("Дополнена работа id=%s «%s»", work.id, work.title)
    return changed


async def get_work(session: AsyncSession, work_id: int) -> Work | None:
    """Возвращает произведение по id с aliases."""
    result = await session.execute(
        select(Work)
        .options(selectinload(Work.aliases))
        .where(Work.id == work_id)
    )
    return result.scalar_one_or_none()


async def create_work(
    session: AsyncSession,
    *,
    title: str,
    author: str | None = None,
    original_title: str | None = None,
    russian_title: str | None = None,
    fandom: str | None = None,
    pairing: str | None = None,
    status: str = WorkStatus.UNKNOWN.value,
    word_count: int | None = None,
    volume_label: str | None = None,
    tags: list[str] | None = None,
    source_url: str | None = None,
    translation_url: str | None = None,
    confirmation_status: str = ConfirmationStatus.NEEDS_REVIEW.value,
    other_titles: str | list[str] | None = None,
    abbreviations: str | list[str] | None = None,
) -> Work:
    """Создаёт произведение и его aliases."""
    bilingual = split_bilingual_title(title)
    if bilingual:
        russian_title = russian_title or bilingual[0]
        original_title = original_title or bilingual[1]

    work = Work(
        title=title.strip(),
        original_title=original_title.strip() if original_title else None,
        russian_title=russian_title.strip() if russian_title else None,
        author=author.strip() if author else None,
        normalized_title=normalize_text(title),
        normalized_author=normalize_text(author) if author else None,
        fandom=fandom,
        pairing=pairing,
        status=status,
        word_count=word_count,
        volume_label=volume_label,
        tags=tags or [],
        source_url=source_url,
        translation_url=translation_url,
        confirmation_status=confirmation_status,
    )
    session.add(work)
    await session.flush()

    alias_pairs: list[tuple[str, str]] = []
    if isinstance(other_titles, str):
        other_list = split_aliases(other_titles)
    else:
        other_list = other_titles or []
    if isinstance(abbreviations, str):
        abbr_list = split_aliases(abbreviations)
    else:
        abbr_list = abbreviations or []

    for alias in other_list:
        alias_pairs.append((alias, "alt"))
    for alias in abbr_list:
        alias_pairs.append((alias, "abbreviation"))

    # Основное и альтернативные официальные названия тоже в aliases
    for extra in (title, original_title, russian_title):
        if extra and normalize_text(extra) != work.normalized_title:
            alias_pairs.append((extra, "alt"))
        elif extra:
            alias_pairs.append((extra, "primary"))

    seen_norm: set[str] = set()
    for raw, alias_type in alias_pairs:
        norm = normalize_text(raw)
        if not norm or norm in seen_norm:
            continue
        seen_norm.add(norm)
        session.add(
            WorkAlias(
                work_id=work.id,
                alias=raw.strip(),
                normalized_alias=norm,
                alias_type=alias_type,
            )
        )

    await session.commit()
    await session.refresh(work)
    logger.info(
        "Создано произведение id=%s «%s» confirmation=%s",
        work.id,
        work.title,
        work.confirmation_status,
    )
    return work


async def confirm_work(session: AsyncSession, work_id: int) -> Work | None:
    """Помечает произведение как подтверждённое."""
    work = await get_work(session, work_id)
    if not work:
        return None
    work.confirmation_status = ConfirmationStatus.CONFIRMED.value
    await session.commit()
    await session.refresh(work)
    logger.info("Произведение id=%s подтверждено", work_id)
    return work


def volume_from_words(word_count: int | None) -> str | None:
    """Грубая метка объёма по числу слов."""
    if word_count is None:
        return None
    if word_count < 10000:
        return "Короткий"
    if word_count < 50000:
        return "Средний"
    return "Длинный"


def parse_work_status(raw: str | None) -> str:
    """Преобразует статус из таблицы/страницы в enum-значение."""
    if not raw:
        return WorkStatus.UNKNOWN.value
    value = raw.strip().lower()
    mapping = {
        "завершён": WorkStatus.COMPLETED.value,
        "завершен": WorkStatus.COMPLETED.value,
        "completed": WorkStatus.COMPLETED.value,
        "complete": WorkStatus.COMPLETED.value,
        "пишется": WorkStatus.IN_PROGRESS.value,
        "in progress": WorkStatus.IN_PROGRESS.value,
        "in_progress": WorkStatus.IN_PROGRESS.value,
        "wip": WorkStatus.IN_PROGRESS.value,
        "unknown": WorkStatus.UNKNOWN.value,
        "неизвестно": WorkStatus.UNKNOWN.value,
    }
    return mapping.get(value, WorkStatus.UNKNOWN.value)
