"""Интеграция с ЮKassa: создание и проверка платежей (+ демо без ключей)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Payment, User
from app.services import promo as promo_service

logger = logging.getLogger(__name__)


class PaymentError(Exception):
    """Ошибка платёжного сервиса."""


def yookassa_keys_configured() -> bool:
    settings = get_settings()
    return bool(settings.yookassa_shop_id and settings.yookassa_secret_key)


def is_demo_payment_mode() -> bool:
    """Тестовый режим без ключей ЮKassa — локальная демо-оплата."""
    settings = get_settings()
    return settings.yookassa_test_mode and not yookassa_keys_configured()


def _configure_yookassa() -> None:
    settings = get_settings()
    if not yookassa_keys_configured():
        raise PaymentError(
            "ЮKassa не настроена: задай YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY в .env"
        )
    from yookassa import Configuration

    Configuration.account_id = settings.yookassa_shop_id
    Configuration.secret_key = settings.yookassa_secret_key


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def resolve_payment_amount(
    session: AsyncSession,
    user: User,
    *,
    promo_code: str | None = None,
) -> tuple[Decimal, int, str | None]:
    """Сумма к оплате с учётом промо.

    Returns:
        (amount, discount_percent, promo_code_or_none)
    """
    settings = get_settings()
    base = Decimal(str(settings.subscription_price_rub))
    code = promo_code or user.promo_code
    discount = 0
    if code:
        promo = await promo_service.get_active_promo(session, code)
        if promo:
            discount = int(promo.discount_percent or 0)
            code = promo.code
        else:
            code = None
    amount = promo_service.price_with_discount(base, discount)
    return amount, discount, code


async def create_subscription_payment(
    session: AsyncSession,
    user: User,
    *,
    promo_code: str | None = None,
) -> Payment:
    """Создаёт платёж (ЮKassa или демо) и сохраняет запись в БД."""
    settings = get_settings()
    amount, discount, resolved_promo = await resolve_payment_amount(
        session, user, promo_code=promo_code
    )

    if is_demo_payment_mode():
        return await _create_demo_payment(
            session,
            user,
            amount=amount,
            promo_code=resolved_promo,
            discount_percent=discount,
        )

    _configure_yookassa()
    from yookassa import Payment as YooPayment

    description = f"FicBrain: годовая подписка ({settings.subscription_period_days} дн.)"
    if discount:
        description += f", скидка {discount}%"
    if settings.yookassa_test_mode:
        description = "[TEST] " + description

    idempotence_key = str(uuid.uuid4())
    metadata = {
        "user_id": str(user.id),
        "telegram_id": str(user.telegram_id),
        "promo_code": (resolved_promo or "")[:64],
        "discount_percent": str(discount),
    }

    try:
        payload = {
            "amount": {
                "value": f"{amount:.2f}",
                "currency": "RUB",
            },
            "confirmation": {
                "type": "redirect",
                "return_url": "https://t.me/",
            },
            "capture": True,
            "description": description[:128],
            "metadata": metadata,
        }
        response = await asyncio.to_thread(
            YooPayment.create, payload, idempotence_key
        )
    except Exception as exc:
        logger.exception("ЮKassa: не удалось создать платёж")
        raise PaymentError(f"Не удалось создать платёж: {exc}") from exc

    confirmation_url = None
    confirmation = getattr(response, "confirmation", None)
    if confirmation is not None:
        confirmation_url = getattr(confirmation, "confirmation_url", None)

    status = getattr(response, "status", None) or "pending"
    payment_id = str(getattr(response, "id", "") or "")
    if not payment_id:
        raise PaymentError("ЮKassa не вернула id платежа")

    is_test = bool(getattr(response, "test", settings.yookassa_test_mode))

    payment = Payment(
        user_id=user.id,
        yookassa_payment_id=payment_id,
        amount=amount,
        currency="RUB",
        status=status,
        is_test=is_test,
        promo_code=resolved_promo,
        confirmation_url=confirmation_url,
    )
    session.add(payment)
    await session.commit()
    await session.refresh(payment)
    logger.info(
        "Создан платёж id=%s yookassa=%s user_id=%s test=%s amount=%s",
        payment.id,
        payment_id,
        user.id,
        is_test,
        amount,
    )
    return payment


async def _create_demo_payment(
    session: AsyncSession,
    user: User,
    *,
    amount: Decimal,
    promo_code: str | None,
    discount_percent: int,
) -> Payment:
    """Локальный демо-платёж без обращения к ЮKassa."""
    payment_id = f"demo-{uuid.uuid4().hex[:16]}"
    payment = Payment(
        user_id=user.id,
        yookassa_payment_id=payment_id,
        amount=amount,
        currency="RUB",
        status="pending",
        is_test=True,
        promo_code=promo_code,
        confirmation_url=None,
    )
    session.add(payment)
    await session.commit()
    await session.refresh(payment)
    logger.info(
        "Демо-платёж id=%s user_id=%s amount=%s discount=%s%%",
        payment.id,
        user.id,
        amount,
        discount_percent,
    )
    return payment


async def get_payment(session: AsyncSession, payment_id: int) -> Payment | None:
    """Платёж по внутреннему id."""
    return await session.get(Payment, payment_id)


async def sync_payment_status(
    session: AsyncSession,
    payment: Payment,
) -> Payment:
    """Проверяет статус (ЮKassa или демо) и при успехе активирует подписку."""
    # Демо-платёж: «Проверить оплату» = успешная оплата
    if str(payment.yookassa_payment_id).startswith("demo-"):
        if payment.status != "succeeded":
            payment.status = "succeeded"
            payment.paid_at = _utcnow()
            payment.is_test = True
            user = await session.get(User, payment.user_id)
            if user:
                await activate_subscription(session, user, payment=payment)
            else:
                await session.commit()
            await session.refresh(payment)
        return payment

    _configure_yookassa()
    from yookassa import Payment as YooPayment

    try:
        remote = await asyncio.to_thread(
            YooPayment.find_one, payment.yookassa_payment_id
        )
    except Exception as exc:
        logger.exception(
            "ЮKassa: не удалось проверить платёж %s",
            payment.yookassa_payment_id,
        )
        raise PaymentError(f"Не удалось проверить оплату: {exc}") from exc

    status = getattr(remote, "status", None) or payment.status
    payment.status = status
    if bool(getattr(remote, "test", False)):
        payment.is_test = True

    if status == "succeeded" and payment.paid_at is None:
        payment.paid_at = _utcnow()
        user = await session.get(User, payment.user_id)
        if user:
            await activate_subscription(session, user, payment=payment)
            logger.info(
                "Оплата подтверждена payment_id=%s user_id=%s test=%s",
                payment.id,
                user.id,
                payment.is_test,
            )
        else:
            await session.commit()
    else:
        await session.commit()

    await session.refresh(payment)
    return payment


async def activate_subscription(
    session: AsyncSession,
    user: User,
    *,
    payment: Payment | None = None,
) -> datetime:
    """Продлевает подписку на SUBSCRIPTION_PERIOD_DAYS от max(now, until)."""
    settings = get_settings()
    now = _utcnow()
    base = now
    if user.subscription_until is not None:
        until = user.subscription_until
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        if until > now:
            base = until
    new_until = base + timedelta(days=settings.subscription_period_days)
    user.subscription_until = new_until
    if payment and payment.promo_code and not user.promo_code:
        user.promo_code = payment.promo_code.upper()
    await session.commit()

    if payment:
        try:
            from app.services import sheets as sheets_service

            await sheets_service.log_attribution(
                telegram_id=user.telegram_id,
                username=user.username,
                promo=payment.promo_code or user.promo_code,
                event="subscribe",
                paid_at=payment.paid_at or now,
                amount=float(payment.amount),
                payment_id=payment.yookassa_payment_id,
                is_test=payment.is_test,
            )
        except Exception:
            logger.exception("Не удалось записать атрибуцию оплаты в Sheets")

    return new_until
