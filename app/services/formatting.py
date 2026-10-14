"""Форматирование сообщений о результатах поиска."""

from __future__ import annotations

from app.keyboards import status_label
from app.models.enums import ReadingStatus, WorkStatus
from app.services.library import status_emoji_line
from app.services.resolution import ResolutionBatch, ResolvedItem


def format_batch_summary(batch: ResolutionBatch) -> str:
    """Сводка: сколько новых / уже в библиотеке."""
    total = len([i for i in batch.items if i.work or i.needs_user_choice])
    new_count = len(batch.new_items)
    want = len(batch.want_items)
    read = len(batch.read_items)
    other = len(batch.other_known)

    lines = [f"Нашла {total} фанфиков ✨", ""]
    lines.append(f"🆕 {new_count} новых")
    if want:
        lines.append(f"🔖 {want} уже хочешь прочитать")
    if read:
        lines.append(f"☑ {read} уже прочитан")
    if other:
        lines.append(f"📖/💀 {other} уже в библиотеке с другим статусом")
    pending = [i for i in batch.items if i.needs_user_choice]
    if pending:
        lines.append(f"❓ {len(pending)} требуют уточнения")
    needs_confirm = [
        i for i in batch.items if i.needs_catalog_confirm and not i.work and not i.needs_user_choice
    ]
    if needs_confirm:
        lines.append(f"❔ {len(needs_confirm)} нужно подтверждение")
    return "\n".join(lines)


def format_batch_list(batch: ResolutionBatch) -> str:
    """Полный список найденного."""
    lines = ["Найденные работы:", ""]
    for idx, item in enumerate(batch.items, start=1):
        title = item.extracted.title
        author = item.extracted.author or (item.work.author if item.work else None)
        head = f"{idx}. {title}"
        if author:
            head += f" — {author}"
        if item.needs_user_choice:
            head += "\n   ❓ нужно уточнить"
        elif item.library_item:
            head += f"\n   {status_emoji_line(item.library_item.reading_status)}"
        elif item.work:
            head += "\n   🆕 новая для тебя"
        elif item.needs_catalog_confirm:
            head += "\n   ❔ нужно подтверждение"
        else:
            head += "\n   ❔ пропущено"
        lines.append(head)
    return "\n".join(lines)


_WORK_STATUS_RU = {
    WorkStatus.COMPLETED.value: "Завершён",
    WorkStatus.IN_PROGRESS.value: "Пишется",
    WorkStatus.UNKNOWN.value: "Неизвестно",
}


def _work_meta_block(work) -> list[str]:
    """Блок метаданных с подписями полей."""
    lines: list[str] = []
    if work.pairing:
        lines.append(f"Пейринг: {work.pairing}")
    if work.fandom:
        lines.append(f"Фандом: {work.fandom}")
    lines.append(f"Статус: {_WORK_STATUS_RU.get(work.status, work.status)}")
    if work.volume_label:
        lines.append(f"Объём: {work.volume_label}")
    if work.tags:
        lines.append(f"Теги: {', '.join(work.tags[:8])}")
    return lines


def format_single_work(item: ResolvedItem) -> str:
    """Карточка одной найденной работы перед добавлением."""
    work = item.work
    if not work:
        return f"Кажется, нашла:\n\n{item.extracted.title}"
    lines = [
        "Кажется, нашла:",
        "",
        work.title,
    ]
    if work.author:
        lines.append(f"Автор: {work.author}")
    meta = _work_meta_block(work)
    if meta:
        lines.append("")
        lines.extend(meta)
    return "\n".join(lines)


def format_library_card(work, reading_status: str, meetings: int) -> str:
    """Текст карточки в библиотеке."""
    lines = [work.title]
    if work.author:
        lines.append(f"Автор: {work.author}")
    lines.append("")
    lines.extend(_work_meta_block(work))
    lines.append("")
    lines.append(f"Твой статус: {status_label(reading_status)}")
    if meetings:
        times = "раз" if meetings % 10 == 1 and meetings % 100 != 11 else "раза" if 2 <= meetings % 10 <= 4 and not 12 <= meetings % 100 <= 14 else "раз"
        lines.append(f"Ты встречала его в рекомендациях {meetings} {times}.")
    return "\n".join(lines)


def format_already_read(title: str) -> str:
    return f"☑ Ты это уже читала.\n\n{title}"
