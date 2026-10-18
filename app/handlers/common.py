"""Базовые команды и меню."""

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app import texts
from app.db.session import async_session_factory
from app.keyboards import main_menu
from app.services import promo as promo_service
from app.services.users import get_or_create_user

logger = logging.getLogger(__name__)
router = Router()


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    state: FSMContext,
    command: CommandObject,
) -> None:
    """Обработчик /start, в т.ч. deep link с промокодом."""
    await state.clear()
    async with async_session_factory() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )
        promo_msg = None
        payload = (command.args or "").strip() if command else ""
        if payload:
            # /start CODE или /start promo_CODE
            ok, promo_msg = await promo_service.attach_promo_to_user(
                session,
                user,
                payload,
                event="start",
                force=False,
            )
            if not ok and "уже привязан" in (promo_msg or ""):
                # при deep link не перезаписываем молча
                pass
            logger.info(
                "Deep link promo user=%s payload=%s ok=%s",
                message.from_user.id,
                payload,
                ok,
            )

    logger.info("Пользователь %s запустил бота", message.from_user.id)
    await message.answer(texts.WELCOME, reply_markup=main_menu())
    if promo_msg:
        await message.answer(promo_msg)


@router.message(F.text == "ℹ️ Как это работает")
@router.message(Command("help"))
async def how_it_works(message: Message) -> None:
    """Справка."""
    await message.answer(texts.HOW_IT_WORKS, reply_markup=main_menu())
