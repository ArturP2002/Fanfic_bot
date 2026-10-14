"""Личная библиотека пользователя."""

from __future__ import annotations

import logging
from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import LibraryItem, Source, SourceWork, Work
from app.models.enums import ReadingStatus, SourceType
from app.services.normalize import normalize_text

logger = logging.getLogger(__name__)

STATUS_LABELS = {
    ReadingStatus.WANT.value: "🔖 Хочу прочитать",
    ReadingStatus.READING.value: "📖 Читаю",
    ReadingStatus.READ.value: "☑ Прочитано",
    ReadingStatus.DROPPED.value: "💀 Бросила",
}


async def get_status_counts(session: AsyncSession, user_id: int) -> dict[str, int]:
    """Возвращает число работ по статусам."""
    result = await session.execute(
        select(LibraryItem.reading_status, func.count())
        .where(LibraryItem.user_id == user_id)
        .group_by(LibraryItem.reading_status)
    )
    counts = Counter({status.value: 0 for status in ReadingStatus})
    for status, count in result.all():
        counts[status] = count
    return dict(counts)


async def list_by_status(
    session: AsyncSession,
    user_id: int,
    status: str,
    page: int = 0,
    page_size: int = 8,
) -> tuple[list[LibraryItem], int]:
    """Постраничный список библиотеки по статусу."""
    total_result = await session.execute(
        select(func.count())
        .select_from(LibraryItem)
        .where(
            LibraryItem.user_id == user_id,
            LibraryItem.reading_status == status,
        )
    )
    total = int(total_result.scalar_one())

    result = await session.execute(
        select(LibraryItem)
        .options(selectinload(LibraryItem.work))
        .where(
            LibraryItem.user_id == user_id,
            LibraryItem.reading_status == status,
        )
        .order_by(LibraryItem.updated_at.desc())
        .offset(page * page_size)
        .limit(page_size)
    )
    return list(result.scalars().all()), total


async def get_library_item(
    session: AsyncSession,
    user_id: int,
    work_id: int,
) -> LibraryItem | None:
    """Возвращает запись библиотеки пользователя по work_id."""
    result = await session.execute(
        select(LibraryItem)
        .options(selectinload(LibraryItem.work).selectinload(Work.aliases))
        .where(
            LibraryItem.user_id == user_id,
            LibraryItem.work_id == work_id,
        )
    )
    return result.scalar_one_or_none()


async def add_to_library(
    session: AsyncSession,
    user_id: int,
    work_id: int,
    status: str = ReadingStatus.WANT.value,
) -> tuple[LibraryItem, bool]:
    """Добавляет работу в библиотеку. Возвращает (item, created)."""
    existing = await get_library_item(session, user_id, work_id)
    if existing:
        logger.info(
            "Работа work_id=%s уже в библиотеке user_id=%s",
            work_id,
            user_id,
        )
        return existing, False

    item = LibraryItem(
        user_id=user_id,
        work_id=work_id,
        reading_status=status,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    logger.info(
        "Добавлена работа work_id=%s в библиотеку user_id=%s",
        work_id,
        user_id,
    )
    # перезагрузка с work
    item = await get_library_item(session, user_id, work_id)
    assert item is not None
    return item, True


async def set_status(
    session: AsyncSession,
    user_id: int,
    work_id: int,
    status: str,
) -> LibraryItem | None:
    """Меняет статус чтения."""
    item = await get_library_item(session, user_id, work_id)
    if not item:
        return None
    item.reading_status = status
    await session.commit()
    await session.refresh(item)
    logger.info(
        "Статус work_id=%s user_id=%s → %s",
        work_id,
        user_id,
        status,
    )
    return item


async def search_library(
    session: AsyncSession,
    user_id: int,
    query: str,
    limit: int = 10,
) -> list[LibraryItem]:
    """Простой поиск по личной библиотеке."""
    norm = normalize_text(query)
    if not norm:
        return []

    result = await session.execute(
        select(LibraryItem)
        .join(Work)
        .options(selectinload(LibraryItem.work))
        .where(LibraryItem.user_id == user_id)
        .limit(200)
    )
    items = list(result.scalars().all())
    matched: list[tuple[int, LibraryItem]] = []
    for item in items:
        work = item.work
        haystacks = [
            normalize_text(work.title),
            normalize_text(work.original_title),
            normalize_text(work.russian_title),
            normalize_text(work.author),
        ]
        score = 0
        for h in haystacks:
            if not h:
                continue
            if norm == h:
                score = max(score, 100)
            elif norm in h or h in norm:
                score = max(score, 80)
        if score:
            matched.append((score, item))
    matched.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in matched[:limit]]


async def count_recommendations(
    session: AsyncSession,
    user_id: int,
    work_id: int,
) -> int:
    """Сколько раз пользователь встречал работу в источниках."""
    result = await session.execute(
        select(func.count())
        .select_from(SourceWork)
        .join(Source)
        .where(
            Source.user_id == user_id,
            SourceWork.work_id == work_id,
        )
    )
    return int(result.scalar_one())


async def create_source(
    session: AsyncSession,
    user_id: int,
    source_type: str,
    *,
    platform: str | None = None,
    url: str | None = None,
    author_handle: str | None = None,
    raw_text: str | None = None,
    work_ids: list[int] | None = None,
) -> Source:
    """Создаёт источник и связи с произведениями."""
    source = Source(
        user_id=user_id,
        source_type=source_type,
        platform=platform,
        url=url,
        author_handle=author_handle,
        raw_text=raw_text,
    )
    session.add(source)
    await session.flush()
    # Уникальные work_id в порядке первого появления — иначе uq_source_work
    unique_work_ids = list(dict.fromkeys(work_ids or []))
    for work_id in unique_work_ids:
        session.add(SourceWork(source_id=source.id, work_id=work_id))
    await session.commit()
    await session.refresh(source)
    logger.info(
        "Создан источник id=%s type=%s works=%s",
        source.id,
        source_type,
        unique_work_ids,
    )
    return source


async def link_works_to_source(
    session: AsyncSession,
    source_id: int,
    work_ids: list[int],
) -> None:
    """Добавляет связи source↔work без дублей."""
    for work_id in work_ids:
        exists = await session.execute(
            select(SourceWork).where(
                SourceWork.source_id == source_id,
                SourceWork.work_id == work_id,
            )
        )
        if exists.scalar_one_or_none():
            continue
        session.add(SourceWork(source_id=source_id, work_id=work_id))
    await session.commit()


def status_emoji_line(status: str) -> str:
    """Короткая строка статуса для сводок."""
    return {
        ReadingStatus.WANT.value: "🔖 уже хочешь прочитать",
        ReadingStatus.READING.value: "📖 уже читаешь",
        ReadingStatus.READ.value: "☑ уже прочитан",
        ReadingStatus.DROPPED.value: "💀 уже в «бросила»",
    }.get(status, status)
