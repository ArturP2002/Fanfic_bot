"""Админ-команды: промокоды блогеров."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app import texts
from app.config import get_settings
from app.db.session import async_session_factory
from app.services import promo as promo_service
from app.states import AdminBloggerStates

logger = logging.getLogger(__name__)
router = Router()


def _is_admin(telegram_id: int) -> bool:
    return telegram_id in get_settings().admin_ids


@router.message(Command("blogger"))
async def blogger_start(message: Message, state: FSMContext) -> None:
    """Старт создания промокода для блогера."""
    if not _is_admin(message.from_user.id):
        await message.answer(texts.ADMIN_ONLY)
        return
    await state.set_state(AdminBloggerStates.waiting_name)
    await message.answer(texts.ADMIN_BLOGGER_ASK_NAME)


@router.message(AdminBloggerStates.waiting_name)
async def blogger_name(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        await message.answer(texts.ADMIN_ONLY)
        return
    name = (message.text or "").strip()
    if not name or name.startswith("/"):
        await message.answer(texts.ADMIN_BLOGGER_ASK_NAME)
        return
    await state.update_data(blogger_name=name)
    await state.set_state(AdminBloggerStates.waiting_code)
    await message.answer(texts.ADMIN_BLOGGER_ASK_CODE.format(name=name))


@router.message(AdminBloggerStates.waiting_code)
async def blogger_code(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        await message.answer(texts.ADMIN_ONLY)
        return
    raw = (message.text or "").strip()
    data = await state.get_data()
    name = data.get("blogger_name") or ""
    code = None if raw.lower() in {"авто", "auto", "gen", "сгенерировать"} else raw

    try:
        async with async_session_factory() as session:
            promo = await promo_service.create_promo(
                session,
                blogger_name=name,
                code=code,
                created_by_telegram_id=message.from_user.id,
            )
    except ValueError as exc:
        await message.answer(str(exc))
        return
    except Exception:
        logger.exception("Ошибка создания промокода")
        await message.answer("Не удалось создать промокод. Смотри логи.")
        await state.clear()
        return

    await state.clear()
    # Попробуем собрать реальную deep-link с username бота
    me = await message.bot.get_me()
    username = me.username or "<bot>"
    link = f"https://t.me/{username}?start=promo_{promo.code}"
    await message.answer(
        texts.ADMIN_BLOGGER_CREATED.format(
            name=promo.blogger_name,
            code=promo.code,
            link=link,
        )
    )
