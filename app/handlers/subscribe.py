"""Подписка и оплата через ЮKassa / демо."""

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


async def _intro_text(session, user, telegram_id: int) -> str:
    settings = get_settings()
    base = settings.subscription_price_rub
    active = access_service.has_active_subscription(user)
    promo = await promo_service.get_user_promo(session, user)

    if promo and promo.discount_percent:
        final = promo_service.price_with_discount(base, promo.discount_percent)
        price_block = (
            f"Продление / цена: {final:.0f} ₽ "
            f"(вместо {base} ₽, скидка {promo.discount_percent}%)"
            if active
            else f"Цена: {final:.0f} ₽ (вместо {base} ₽, скидка {promo.discount_percent}%)"
        )
        promo_block = f"\nПромокод: {promo.code}"
    elif user.promo_code:
        price_block = (
            f"Продление: {base} ₽ / год" if active else f"Цена: {base} ₽ / год"
        )
        promo_block = f"\nПромокод: {user.promo_code}"
    else:
        price_block = (
            f"Продление: {base} ₽ / год" if active else f"Цена: {base} ₽ / год"
        )
        promo_block = ""

    status = access_service.subscription_status_text(user, telegram_id)

    # Длинную демо-подсказку не дублируем, если подписка уже есть
    if active:
        mode_block = ""
    elif payments_service.is_demo_payment_mode():
        mode_block = texts.SUBSCRIBE_DEMO_HINT
    elif settings.yookassa_test_mode:
        mode_block = texts.SUBSCRIBE_TEST_HINT
    else:
        mode_block = ""

    return texts.SUBSCRIBE_INTRO.format(
        price_block=price_block,
        status=status,
        promo_block=promo_block,
        mode_block=mode_block,
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
        text = await _intro_text(session, user, message.from_user.id)
        demo = payments_service.is_demo_payment_mode()
        has_sub = access_service.has_active_subscription(user)
    await message.answer(
        text,
        reply_markup=subscribe_keyboard(demo=demo, has_subscription=has_sub),
    )


@router.callback_query(F.data == "pay:create")
async def create_payment(callback: CallbackQuery, state: FSMContext) -> None:
    """Создать платёж и показать ссылку ЮKassa или демо."""
    await state.clear()
    demo = payments_service.is_demo_payment_mode()

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
        amount = payment.amount
        is_demo = str(payment.yookassa_payment_id).startswith("demo-")

    await callback.answer()
    if is_demo or demo:
        await callback.message.answer(
            texts.PAYMENT_CREATED_DEMO.format(amount=f"{amount:.0f}"),
            reply_markup=subscribe_keyboard(
                payment_id=payment_id,
                demo=True,
            ),
        )
        return

    if not url:
        await callback.message.answer(
            "Не удалось получить ссылку на оплату. Попробуй ещё раз."
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
    """Проверить статус платежа в ЮKassa или подтвердить демо."""
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
        is_demo = str(payment.yookassa_payment_id).startswith("demo-")
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
        tpl = texts.PAYMENT_SUCCEEDED_DEMO if is_demo else texts.PAYMENT_SUCCEEDED
        await callback.message.answer(
            tpl.format(until=until_str),
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
        demo = payments_service.is_demo_payment_mode()
        has_sub = access_service.has_active_subscription(user)

    await state.clear()
    if ok:
        await message.answer(msg)
        await message.answer(
            "Теперь можно нажать «Оплатить» или «Продлить».",
            reply_markup=subscribe_keyboard(demo=demo, has_subscription=has_sub),
        )
    else:
        await message.answer(msg or texts.PROMO_INVALID)
