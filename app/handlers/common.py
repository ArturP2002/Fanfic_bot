"""Базовые команды и меню."""

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.db.session import async_session_factory
from app.keyboards import main_menu
from app.services.users import get_or_create_user
from app import texts

logger = logging.getLogger(__name__)
router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    """Обработчик /start."""
    await state.clear()
    async with async_session_factory() as session:
        await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )
    logger.info("Пользователь %s запустил бота", message.from_user.id)
    await message.answer(texts.WELCOME, reply_markup=main_menu())


@router.message(F.text == "ℹ️ Как это работает")
@router.message(Command("help"))
async def how_it_works(message: Message) -> None:
    """Справка."""
    await message.answer(texts.HOW_IT_WORKS, reply_markup=main_menu())
