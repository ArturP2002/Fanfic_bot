"""Форматирование сообщений о результатах поиска."""

from __future__ import annotations

from typing import Any

from app.keyboards import status_label
from app.models.enums import ReadingStatus, WorkStatus
from app.services.library import status_emoji_line
from app.services.resolution import ResolutionBatch, ResolvedItem


def work_has_rich_meta(work: Any) -> bool:
    """True, если у работы есть поля досье помимо title/author/unknown status."""
    if not work:
        return False
    if work.pairing or work.fandom or work.volume_label:
        return True
    if work.tags:
        return True
    status = getattr(work, "status", None)
    if status and status != WorkStatus.UNKNOWN.value:
        return True
    return False


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


def brief_meta_lines(work: Any, *, indent: str = "   ") -> list[str]:
    """Краткое досье для списков (1–3 строки)."""
    if not work:
        return []
    parts: list[str] = []
    if work.pairing:
        parts.append(f"{indent}Пейринг: {work.pairing}")
    if work.fandom:
        parts.append(f"{indent}Фандом: {work.fandom}")
    if work.volume_label:
        parts.append(f"{indent}Объём: {work.volume_label}")
    elif work.status and work.status != WorkStatus.UNKNOWN.value:
        parts.append(
            f"{indent}Статус: {_WORK_STATUS_RU.get(work.status, work.status)}"
        )
    return parts[:3]


def work_meta_snapshot(work: Any) -> dict[str, Any]:
    """Сериализуемые поля досье для batch_store."""
    if not work:
        return {
            "pairing": None,
            "fandom": None,
            "status": None,
            "volume_label": None,
            "tags": None,
            "has_meta": False,
        }
    return {
        "pairing": work.pairing,
        "fandom": work.fandom,
        "status": work.status,
        "volume_label": work.volume_label,
        "tags": list(work.tags[:8]) if work.tags else None,
        "has_meta": work_has_rich_meta(work),
    }


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
    """Полный список найденного с кратким досье."""
    lines = ["Найденные работы:", ""]
    for idx, item in enumerate(batch.items, start=1):
        title = (item.work.title if item.work else None) or item.extracted.title
        author = (item.work.author if item.work else None) or item.extracted.author
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
        if item.work and work_has_rich_meta(item.work):
            lines.extend(brief_meta_lines(item.work))
        elif item.work or item.needs_catalog_confirm:
            lines.append("   пока только название; метаданные появятся после обогащения / ссылки")
    return "\n".join(lines)


def format_batch_list_from_payload(payload: dict[str, Any]) -> str:
    """Список из сериализованного batch (callback «Посмотреть все»)."""
    lines = ["Найденные работы:", ""]
    for idx, item in enumerate(payload.get("items") or [], start=1):
        title = item.get("title") or "?"
        author = item.get("author")
        head = f"{idx}. {title}"
        if author:
            head += f" — {author}"
        status = item.get("library_status")
        if status == ReadingStatus.READ.value:
            head += "\n   ☑ уже прочитан"
        elif status == ReadingStatus.WANT.value:
            head += "\n   🔖 уже хочешь прочитать"
        elif item.get("needs_user_choice"):
            head += "\n   ❓ нужно уточнить"
        elif item.get("work_id") and not status:
            head += "\n   🆕 новая"
        elif item.get("needs_catalog_confirm"):
            head += "\n   ❔ нужно подтверждение"
        lines.append(head)
        if item.get("has_meta"):
            stub = type(
                "W",
                (),
                {
                    "pairing": item.get("pairing"),
                    "fandom": item.get("fandom"),
                    "volume_label": item.get("volume_label"),
                    "status": item.get("status"),
                    "tags": item.get("tags"),
                },
            )()
            lines.extend(brief_meta_lines(stub))
        elif item.get("work_id") or item.get("needs_catalog_confirm"):
            lines.append(
                "   пока только название; метаданные появятся после обогащения / ссылки"
            )
    return "\n".join(lines)


_WORK_STATUS_RU = {
    WorkStatus.COMPLETED.value: "Завершён",
    WorkStatus.IN_PROGRESS.value: "Пишется",
    WorkStatus.UNKNOWN.value: "Неизвестно",
}


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
    if work_has_rich_meta(work):
        lines.append("")
        lines.extend(_work_meta_block(work))
    else:
        lines.append("")
        lines.append(
            "Метаданных пока нет — только название. "
            "Пришли ссылку AO3/Ficbook, чтобы дополнить досье."
        )
    return "\n".join(lines)


def format_catalog_confirm(item: ResolvedItem) -> str:
    """Сообщение подтверждения нового/сомнительного фанфика."""
    work = item.work
    lines = ["Похоже, нашла новый для FicBrain фанфик:", ""]
    if work:
        lines.append(work.title)
        if work.author:
            lines.append(f"Автор: {work.author}")
        if work_has_rich_meta(work):
            lines.append("")
            lines.extend(_work_meta_block(work))
        else:
            lines.append("")
            lines.append("Метаданных пока нет — только название.")
    else:
        lines.append(item.extracted.title)
        if item.extracted.author:
            lines.append(f"Автор: {item.extracted.author}")
        lines.append("")
        lines.append("Метаданных пока нет — только название.")
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
        times = (
            "раз"
            if meetings % 10 == 1 and meetings % 100 != 11
            else "раза"
            if 2 <= meetings % 10 <= 4 and not 12 <= meetings % 100 <= 14
            else "раз"
        )
        lines.append(f"Ты встречала его в рекомендациях {meetings} {times}.")
    return "\n".join(lines)


def format_already_read(title: str) -> str:
    return f"☑ Ты это уже читала.\n\n{title}"
