"""Личная библиотека и поиск."""

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import texts
from app.db.session import async_session_factory
from app.keyboards import (
    library_list_keyboard,
    library_overview,
    main_menu,
    work_card_keyboard,
)
from app.services import library as library_service
from app.services.formatting import format_library_card
from app.services.users import get_or_create_user
from app.services.works import get_work
from app.states import LibraryStates

logger = logging.getLogger(__name__)
router = Router()


@router.message(F.text == "📚 Моя библиотека")
async def open_library(message: Message, state: FSMContext) -> None:
    """Открывает обзор библиотеки."""
    await state.clear()
    async with async_session_factory() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )
        counts = await library_service.get_status_counts(session, user.id)
    total = sum(counts.values())
    if total == 0:
        await message.answer(texts.LIBRARY_EMPTY, reply_markup=main_menu())
        return
    await message.answer(
        "📚 Моя библиотека",
        reply_markup=library_overview(counts),
    )


@router.callback_query(F.data == "lib:home")
async def lib_home(callback: CallbackQuery) -> None:
    """Возврат к обзору библиотеки."""
    async with async_session_factory() as session:
        user = await get_or_create_user(session, telegram_id=callback.from_user.id)
        counts = await library_service.get_status_counts(session, user.id)
    await callback.message.edit_text(
        "📚 Моя библиотека",
        reply_markup=library_overview(counts),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("lib:status:"))
async def lib_status(callback: CallbackQuery) -> None:
    """Список работ по статусу с пагинацией."""
    _, _, status, page_s = callback.data.split(":")
    page = int(page_s)
    async with async_session_factory() as session:
        user = await get_or_create_user(session, telegram_id=callback.from_user.id)
        items, total = await library_service.list_by_status(
            session, user.id, status, page=page
        )
    if total == 0:
        await callback.answer("В этой категории пока пусто", show_alert=True)
        return
    title = library_service.STATUS_LABELS.get(status, status)
    await callback.message.edit_text(
        f"{title}\nСтраница {page + 1}",
        reply_markup=library_list_keyboard(items, status, page, total),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("lib:card:"))
async def lib_card(callback: CallbackQuery) -> None:
    """Карточка произведения."""
    work_id = int(callback.data.split(":")[2])
    async with async_session_factory() as session:
        user = await get_or_create_user(session, telegram_id=callback.from_user.id)
        item = await library_service.get_library_item(session, user.id, work_id)
        if not item:
            await callback.answer("Работа не найдена в библиотеке", show_alert=True)
            return
        meetings = await library_service.count_recommendations(
            session, user.id, work_id
        )
        text = format_library_card(item.work, item.reading_status, meetings)
        keyboard = work_card_keyboard(
            work_id, item.work.source_url, item.work.translation_url
        )
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("lib:set:"))
async def lib_set_status(callback: CallbackQuery) -> None:
    """Смена статуса чтения."""
    _, _, work_id_s, status = callback.data.split(":")
    work_id = int(work_id_s)
    async with async_session_factory() as session:
        user = await get_or_create_user(session, telegram_id=callback.from_user.id)
        item = await library_service.set_status(session, user.id, work_id, status)
        if not item:
            await callback.answer("Не удалось обновить статус", show_alert=True)
            return
        meetings = await library_service.count_recommendations(
            session, user.id, work_id
        )
        text = format_library_card(item.work, item.reading_status, meetings)
        keyboard = work_card_keyboard(
            work_id, item.work.source_url, item.work.translation_url
        )
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer("Статус обновлён")


@router.callback_query(F.data == "lib:search")
async def lib_search_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Запрос поисковой строки."""
    await state.set_state(LibraryStates.waiting_query)
    await callback.message.answer(texts.SEARCH_PROMPT)
    await callback.answer()


@router.message(LibraryStates.waiting_query)
async def lib_search_query(message: Message, state: FSMContext) -> None:
    """Поиск по библиотеке."""
    query = (message.text or "").strip()
    await state.clear()
    async with async_session_factory() as session:
        user = await get_or_create_user(session, telegram_id=message.from_user.id)
        items = await library_service.search_library(session, user.id, query)
        if not items:
            await message.answer("В библиотеке ничего не нашлось.", reply_markup=main_menu())
            return
        # Показываем первую карточку, остальные кратко
        first = items[0]
        meetings = await library_service.count_recommendations(
            session, user.id, first.work_id
        )
        text = format_library_card(first.work, first.reading_status, meetings)
        await message.answer(
            text,
            reply_markup=work_card_keyboard(
                first.work_id, first.work.source_url, first.work.translation_url
            ),
        )
        if len(items) > 1:
            extra = "\n".join(
                f"• {i.work.title}" for i in items[1:6]
            )
            await message.answer(f"Ещё совпадения:\n{extra}")
