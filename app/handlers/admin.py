"""Админ-команды: промокоды блогеров и диагностика."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app import texts
from app.config import get_settings
from app.db.session import async_session_factory
from app.services import promo as promo_service
from app.services import sheets as sheets_service
from app.states import AdminBloggerStates

logger = logging.getLogger(__name__)
router = Router()


def _is_admin(telegram_id: int) -> bool:
    return telegram_id in get_settings().admin_ids


@router.message(Command("admin"))
async def admin_help(message: Message) -> None:
    """Список админ-команд."""
    if not _is_admin(message.from_user.id):
        await message.answer(texts.ADMIN_ONLY)
        return
    await message.answer(texts.ADMIN_HELP)


@router.message(Command("sheets_test"))
async def sheets_test(message: Message) -> None:
    """Проверка записи в Google Sheets."""
    if not _is_admin(message.from_user.id):
        await message.answer(texts.ADMIN_ONLY)
        return
    await message.answer("Проверяю Google Sheets…\n" + sheets_service.sheets_status())
    try:
        result = await sheets_service.ping_sheets()
        await message.answer("✅ " + result)
    except sheets_service.SheetsError as exc:
        await message.answer(f"❌ {exc}")
    except Exception as exc:
        logger.exception("sheets_test failed")
        await message.answer(f"❌ Неожиданная ошибка: {exc}")


@router.message(Command("blogger"))
async def blogger_start(message: Message, state: FSMContext) -> None:
    """Старт создания промокода для блогера."""
    if not _is_admin(message.from_user.id):
        await message.answer(texts.ADMIN_ONLY)
        return
    await state.set_state(AdminBloggerStates.waiting_name)
    await message.answer(texts.ADMIN_BLOGGER_ASK_NAME)


@router.message(Command("promos"))
async def list_promos(message: Message) -> None:
    """Список активных промокодов."""
    if not _is_admin(message.from_user.id):
        await message.answer(texts.ADMIN_ONLY)
        return
    async with async_session_factory() as session:
        promos = await promo_service.list_active_promos(session)
    if not promos:
        await message.answer(texts.ADMIN_PROMOS_EMPTY)
        return
    lines = ["Активные промокоды:", ""]
    for p in promos:
        lines.append(
            f"• {p.code} — {p.blogger_name}, скидка {p.discount_percent}%"
        )
    lines.append("")
    lines.append("Отключить: /promo_off КОД")
    await message.answer("\n".join(lines))


@router.message(Command("promo_off"))
async def promo_off(message: Message, command: CommandObject) -> None:
    """Деактивировать промокод."""
    if not _is_admin(message.from_user.id):
        await message.answer(texts.ADMIN_ONLY)
        return
    code = (command.args or "").strip()
    if not code:
        await message.answer(texts.ADMIN_PROMO_OFF_USAGE)
        return
    try:
        async with async_session_factory() as session:
            promo, cleared = await promo_service.deactivate_promo(session, code)
    except ValueError as exc:
        await message.answer(str(exc))
        return
    except Exception:
        logger.exception("Ошибка деактивации промокода")
        await message.answer("Не удалось отключить промокод.")
        return
    await message.answer(
        texts.ADMIN_PROMO_OFF_DONE.format(code=promo.code, cleared=cleared)
    )


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
    if raw.lower() in {"авто", "auto", "gen", "сгенерировать"}:
        code = promo_service.generate_code()
    else:
        code = promo_service.normalize_code(raw)
        if not promo_service.is_valid_code_format(code):
            await message.answer(
                "Код: 3–32 символа (латиница, цифры, _ или -). Или напиши «авто»."
            )
            return

    await state.update_data(promo_code=code)
    await state.set_state(AdminBloggerStates.waiting_discount)
    await message.answer(texts.ADMIN_BLOGGER_ASK_DISCOUNT.format(code=code))


@router.message(AdminBloggerStates.waiting_discount)
async def blogger_discount(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        await message.answer(texts.ADMIN_ONLY)
        return

    try:
        discount = promo_service.parse_discount_percent(message.text or "")
    except ValueError as exc:
        await message.answer(str(exc))
        return

    data = await state.get_data()
    name = data.get("blogger_name") or ""
    code = data.get("promo_code")

    try:
        async with async_session_factory() as session:
            promo, sheets_error = await promo_service.create_promo(
                session,
                blogger_name=name,
                code=code,
                discount_percent=discount,
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
    me = await message.bot.get_me()
    username = me.username or "<bot>"
    link = f"https://t.me/{username}?start=promo_{promo.code}"
    if sheets_error:
        sheets_line = f"⚠️ Google Sheets: не записалось\n{sheets_error}"
    else:
        sheets_line = "✅ Записано в Google Sheets (лист «Промокоды»)."
    await message.answer(
        texts.ADMIN_BLOGGER_CREATED.format(
            name=promo.blogger_name,
            code=promo.code,
            discount=promo.discount_percent,
            link=link,
            sheets_line=sheets_line,
        )
    )
