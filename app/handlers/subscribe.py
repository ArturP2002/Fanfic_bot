"""Подписка и оплата через ЮKassa."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import texts
from app.config import get_settings
from app.db.session import async_session_factory
from app.keyboards import main_menu, subscribe_keyboard
from app.services import access as access_service
from app.services import payments as payments_service
from app.services import promo as promo_service
from app.services.users import get_or_create_user
from app.states import SubscribeStates

logger = logging.getLogger(__name__)
router = Router()


def _intro_text(user, telegram_id: int) -> str:
    settings = get_settings()
    status = access_service.subscription_status_text(user, telegram_id)
    mode_hint = (
        texts.SUBSCRIBE_TEST_HINT
        if settings.yookassa_test_mode
        else texts.SUBSCRIBE_LIVE_HINT
    )
    promo_line = ""
    if user.promo_code:
        promo_line = f"\nПромокод: {user.promo_code}"
    return texts.SUBSCRIBE_INTRO.format(
        price=settings.subscription_price_rub,
        status=status + promo_line,
        mode_hint=mode_hint,
    )


@router.message(F.text == "💎 Подписка")
@router.message(Command("subscribe"))
async def show_subscribe(message: Message, state: FSMContext) -> None:
    """Экран подписки."""
    await state.clear()
    async with async_session_factory() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )
        access_service.ensure_free_uses_initialized(user)
        await session.commit()
        text = _intro_text(user, message.from_user.id)
    await message.answer(
        text,
        reply_markup=subscribe_keyboard(),
    )


@router.callback_query(F.data == "pay:create")
async def create_payment(callback: CallbackQuery, state: FSMContext) -> None:
    """Создать платёж и показать ссылку ЮKassa."""
    await state.clear()
    settings = get_settings()
    if not settings.yookassa_shop_id or not settings.yookassa_secret_key:
        await callback.answer()
        await callback.message.answer(texts.YOOKASSA_NOT_CONFIGURED)
        return

    async with async_session_factory() as session:
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            first_name=callback.from_user.first_name,
        )
        try:
            payment = await payments_service.create_subscription_payment(
                session,
                user,
                promo_code=user.promo_code,
            )
        except payments_service.PaymentError as exc:
            logger.warning("Создание платежа: %s", exc)
            await callback.answer()
            await callback.message.answer(str(exc) or texts.PAYMENT_ERROR)
            return

        url = payment.confirmation_url
        payment_id = payment.id

    await callback.answer()
    if not url:
        await callback.message.answer(
            "ЮKassa не вернула ссылку на оплату. Проверь ключи магазина."
        )
        return
    await callback.message.answer(
        texts.PAYMENT_CREATED,
        reply_markup=subscribe_keyboard(
            payment_id=payment_id,
            confirmation_url=url,
        ),
    )


@router.callback_query(F.data.startswith("pay:check:"))
async def check_payment(callback: CallbackQuery) -> None:
    """Проверить статус платежа в ЮKassa."""
    try:
        payment_id = int(callback.data.split(":")[-1])
    except (ValueError, IndexError):
        await callback.answer("Некорректный платёж", show_alert=True)
        return

    async with async_session_factory() as session:
        payment = await payments_service.get_payment(session, payment_id)
        if not payment:
            await callback.answer("Платёж не найден", show_alert=True)
            return
        user = await get_or_create_user(session, telegram_id=callback.from_user.id)
        if payment.user_id != user.id:
            await callback.answer("Это чужой платёж", show_alert=True)
            return
        try:
            payment = await payments_service.sync_payment_status(session, payment)
        except payments_service.PaymentError:
            await callback.answer()
            await callback.message.answer(texts.PAYMENT_ERROR)
            return

        status = payment.status
        await session.refresh(user)
        until = user.subscription_until

    await callback.answer()
    if status == "succeeded":
        until_str = until.strftime("%d.%m.%Y") if until else "—"
        await callback.message.answer(
            texts.PAYMENT_SUCCEEDED.format(until=until_str),
            reply_markup=main_menu(),
        )
    elif status in {"canceled", "cancelled"}:
        await callback.message.answer(texts.PAYMENT_CANCELED)
    else:
        await callback.message.answer(texts.PAYMENT_PENDING)


@router.callback_query(F.data == "pay:promo")
async def ask_promo(callback: CallbackQuery, state: FSMContext) -> None:
    """Запросить промокод перед оплатой."""
    await state.set_state(SubscribeStates.waiting_promo)
    await callback.answer()
    await callback.message.answer(texts.PROMO_ASK)


@router.message(SubscribeStates.waiting_promo)
async def save_promo_on_subscribe(message: Message, state: FSMContext) -> None:
    """Сохранить промокод при оплате (можно перезаписать)."""
    code = (message.text or "").strip()
    if not code:
        await message.answer(texts.PROMO_ASK)
        return

    async with async_session_factory() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )
        ok, msg = await promo_service.attach_promo_to_user(
            session,
            user,
            code,
            event="subscribe_promo",
            force=True,
        )

    await state.clear()
    if ok:
        await message.answer(msg)
        await message.answer(
            "Можешь нажать «Оплатить», чтобы получить ссылку ЮKassa.",
            reply_markup=subscribe_keyboard(),
        )
    else:
        await message.answer(msg or texts.PROMO_INVALID)
