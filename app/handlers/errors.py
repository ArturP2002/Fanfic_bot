"""Сообщения об ошибках распознавания."""

import json
import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import texts
from app.config import get_settings
from app.db.session import async_session_factory
from app.models import ErrorReport
from app.services.users import get_or_create_user
from app.states import ErrorStates

logger = logging.getLogger(__name__)
router = Router()


@router.callback_query(F.data.startswith("err:"))
async def report_error_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Запрос описания ошибки."""
    payload = callback.data
    await state.set_state(ErrorStates.waiting_description)
    await state.update_data(error_ref=payload)
    await callback.message.answer(
        "Опиши, что не так (неправильное название, дубль, пропуск в ролике и т.п.):"
    )
    await callback.answer()


@router.message(ErrorStates.waiting_description)
async def report_error_save(message: Message, state: FSMContext, bot: Bot) -> None:
    """Сохраняет отчёт об ошибке и уведомляет админов."""
    data = await state.get_data()
    ref = data.get("error_ref", "")
    await state.clear()

    work_id = None
    parts = ref.split(":")
    if len(parts) >= 2 and parts[1].isdigit():
        work_id = int(parts[1])

    description = (message.text or "").strip()
    report_id: int | None = None
    username = message.from_user.username if message.from_user else None
    telegram_id = message.from_user.id if message.from_user else None

    async with async_session_factory() as session:
        user = await get_or_create_user(session, telegram_id=message.from_user.id)
        report = ErrorReport(
            user_id=user.id,
            work_id=work_id,
            description=description,
            context_json=json.dumps({"ref": ref}, ensure_ascii=False),
        )
        session.add(report)
        await session.commit()
        await session.refresh(report)
        report_id = report.id
        logger.info(
            "Сохранён отчёт об ошибке id=%s user_id=%s",
            report.id,
            user.id,
        )

    await message.answer(texts.ERROR_REPORT_THANKS)

    settings = get_settings()
    admin_ids = settings.admin_ids
    if not admin_ids:
        logger.warning(
            "ADMIN_TELEGRAM_IDS пуст — отчёт #%s сохранён, но админам не отправлен",
            report_id,
        )
        return

    admin_text = (
        "⚠️ Жалоба из FicBrain\n\n"
        f"Отчёт: #{report_id}\n"
        f"Пользователь: @{username or '—'} (id {telegram_id})\n"
        f"Контекст: {ref}\n"
        f"work_id: {work_id or '—'}\n\n"
        f"Описание:\n{description}"
    )
    for admin_id in admin_ids:
        try:
            await bot.send_message(admin_id, admin_text)
            logger.info("Отчёт #%s отправлен админу %s", report_id, admin_id)
        except Exception:
            logger.exception("Не удалось отправить отчёт #%s админу %s", report_id, admin_id)
