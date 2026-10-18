"""Клавиатуры Telegram-интерфейса."""

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.models.enums import ReadingStatus
from app.services.library import STATUS_LABELS


def main_menu() -> ReplyKeyboardMarkup:
    """Главное меню."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Добавить"), KeyboardButton(text="📚 Моя библиотека")],
            [KeyboardButton(text="💎 Подписка"), KeyboardButton(text="ℹ️ Как это работает")],
        ],
        resize_keyboard=True,
    )


def subscribe_keyboard(
    *,
    payment_id: int | None = None,
    confirmation_url: str | None = None,
) -> InlineKeyboardMarkup:
    """Клавиатура экрана подписки."""
    rows: list[list[InlineKeyboardButton]] = []
    if confirmation_url and payment_id is not None:
        rows.append(
            [InlineKeyboardButton(text="Оплатить в ЮKassa", url=confirmation_url)]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="Проверить оплату",
                    callback_data=f"pay:check:{payment_id}",
                )
            ]
        )
    else:
        rows.append(
            [InlineKeyboardButton(text="Оплатить", callback_data="pay:create")]
        )
    rows.append(
        [InlineKeyboardButton(text="Есть промокод", callback_data="pay:promo")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def choose_platform_hit_keyboard(
    token: str, index: int, hits: list
) -> InlineKeyboardMarkup:
    """Выбор кандидата с площадки (AO3/Ficbook) по URL."""
    rows = []
    for hit_idx, hit in enumerate(hits[:5]):
        label = hit.title if hasattr(hit, "title") else hit.get("title", "?")
        author = hit.author if hasattr(hit, "author") else hit.get("author")
        platform = hit.platform if hasattr(hit, "platform") else hit.get("platform", "")
        if author:
            label = f"{label} — {author}"
        if platform:
            label = f"[{platform}] {label}"
        if len(label) > 60:
            label = label[:57] + "…"
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"phit:pick:{token}:{index}:{hit_idx}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="Ничего из этого",
                callback_data=f"phit:none:{token}:{index}",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def library_overview(counts: dict[str, int]) -> InlineKeyboardMarkup:
    """Кнопки категорий библиотеки."""
    rows = [
        [
            InlineKeyboardButton(
                text=f"🔖 Хочу прочитать — {counts.get(ReadingStatus.WANT.value, 0)}",
                callback_data=f"lib:status:{ReadingStatus.WANT.value}:0",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"📖 Читаю — {counts.get(ReadingStatus.READING.value, 0)}",
                callback_data=f"lib:status:{ReadingStatus.READING.value}:0",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"☑ Прочитано — {counts.get(ReadingStatus.READ.value, 0)}",
                callback_data=f"lib:status:{ReadingStatus.READ.value}:0",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"💀 Бросила — {counts.get(ReadingStatus.DROPPED.value, 0)}",
                callback_data=f"lib:status:{ReadingStatus.DROPPED.value}:0",
            )
        ],
        [InlineKeyboardButton(text="🔍 Поиск", callback_data="lib:search")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def library_list_keyboard(
    items: list,
    status: str,
    page: int,
    total: int,
    page_size: int = 8,
) -> InlineKeyboardMarkup:
    """Список работ категории с пагинацией."""
    rows = []
    for item in items:
        title = item.work.title
        if len(title) > 40:
            title = title[:37] + "…"
        rows.append(
            [
                InlineKeyboardButton(
                    text=title,
                    callback_data=f"lib:card:{item.work_id}",
                )
            ]
        )
    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=f"lib:status:{status}:{page - 1}",
            )
        )
    max_page = max((total - 1) // page_size, 0)
    if page < max_page:
        nav.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=f"lib:status:{status}:{page + 1}",
            )
        )
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="lib:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def work_card_keyboard(
    work_id: int,
    source_url: str | None = None,
    translation_url: str | None = None,
) -> InlineKeyboardMarkup:
    """Кнопки карточки произведения."""
    rows = [
        [
            InlineKeyboardButton(
                text="Начала читать",
                callback_data=f"lib:set:{work_id}:{ReadingStatus.READING.value}",
            ),
            InlineKeyboardButton(
                text="Прочитала",
                callback_data=f"lib:set:{work_id}:{ReadingStatus.READ.value}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="Бросила",
                callback_data=f"lib:set:{work_id}:{ReadingStatus.DROPPED.value}",
            ),
            InlineKeyboardButton(
                text="Хочу прочитать",
                callback_data=f"lib:set:{work_id}:{ReadingStatus.WANT.value}",
            ),
        ],
    ]
    if translation_url:
        rows.append([InlineKeyboardButton(text="Читать перевод", url=translation_url)])
        if source_url:
            rows.append([InlineKeyboardButton(text="Оригинал", url=source_url)])
    elif source_url:
        rows.append([InlineKeyboardButton(text="Открыть источник", url=source_url)])
    rows.append(
        [InlineKeyboardButton(text="⚠️ Сообщить об ошибке", callback_data=f"err:{work_id}")]
    )
    rows.append([InlineKeyboardButton(text="◀️ К библиотеке", callback_data="lib:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def batch_result_keyboard(token: str, new_count: int) -> InlineKeyboardMarkup:
    """Кнопки после пакетного поиска."""
    rows = []
    if new_count > 0:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Добавить {new_count} новых",
                    callback_data=f"batch:add:{token}",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="Посмотреть все", callback_data=f"batch:view:{token}")]
    )
    rows.append(
        [InlineKeyboardButton(text="⚠️ Сообщить об ошибке", callback_data=f"err:batch:{token}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_match_keyboard(token: str, index: int) -> InlineKeyboardMarkup:
    """Подтверждение сомнительного матчинга."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, это он",
                    callback_data=f"match:yes:{token}:{index}",
                ),
                InlineKeyboardButton(
                    text="Нет",
                    callback_data=f"match:no:{token}:{index}",
                ),
            ]
        ]
    )


def choose_candidate_keyboard(token: str, index: int, candidates: list) -> InlineKeyboardMarkup:
    """Выбор из нескольких кандидатов."""
    rows = []
    for work in candidates:
        label = work.title
        if work.author:
            label = f"{work.title} — {work.author}"
        if len(label) > 60:
            label = label[:57] + "…"
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"match:pick:{token}:{index}:{work.id}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="Ничего из этого",
                callback_data=f"match:none:{token}:{index}",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def single_add_keyboard(work_id: int) -> InlineKeyboardMarkup:
    """Добавление одной работы."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Добавить", callback_data=f"add:one:{work_id}"),
                InlineKeyboardButton(text="Не то", callback_data=f"add:reject:{work_id}"),
            ],
            [
                InlineKeyboardButton(
                    text="⚠️ Сообщить об ошибке",
                    callback_data=f"err:{work_id}",
                )
            ],
        ]
    )


def status_label(status: str) -> str:
    """Человекочитаемый статус."""
    return STATUS_LABELS.get(status, status)
