"""Промокоды блогеров."""

from __future__ import annotations

import logging
import re
import secrets
import string
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import PromoCode, User

logger = logging.getLogger(__name__)

_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")


def normalize_code(raw: str) -> str:
    """Нормализует код (верхний регистр, без префикса promo_)."""
    code = (raw or "").strip()
    if code.lower().startswith("promo_"):
        code = code[6:]
    return code.upper()


def is_valid_code_format(code: str) -> bool:
    return bool(_CODE_RE.match(code))


def generate_code(length: int = 8) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def parse_discount_percent(raw: str) -> int:
    """Парсит скидку 0–100 из текста пользователя."""
    text = (raw or "").strip().replace("%", "").replace(",", ".")
    if not text:
        raise ValueError("Укажи число от 0 до 100")
    try:
        value = int(float(text))
    except ValueError as exc:
        raise ValueError("Укажи целое число от 0 до 100") from exc
    if value < 0 or value > 100:
        raise ValueError("Скидка должна быть от 0 до 100%")
    return value


def price_with_discount(base_rub: int | Decimal, discount_percent: int) -> Decimal:
    """Цена после скидки (минимум 1 ₽, если база > 0 и скидка < 100)."""
    base = Decimal(str(base_rub))
    pct = max(0, min(100, int(discount_percent)))
    if pct <= 0:
        return base.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if pct >= 100:
        return Decimal("0.00")
    amount = (base * (Decimal(100) - Decimal(pct)) / Decimal(100)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    if base > 0 and amount < Decimal("1.00"):
        return Decimal("1.00")
    return amount


async def get_active_promo(session: AsyncSession, code: str) -> PromoCode | None:
    """Активный промокод по коду."""
    normalized = normalize_code(code)
    if not normalized:
        return None
    result = await session.execute(
        select(PromoCode).where(
            PromoCode.code == normalized,
            PromoCode.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def get_user_promo(session: AsyncSession, user: User) -> PromoCode | None:
    """Активный промокод, привязанный к пользователю."""
    if not user.promo_code:
        return None
    return await get_active_promo(session, user.promo_code)


async def create_promo(
    session: AsyncSession,
    *,
    blogger_name: str,
    code: str | None,
    discount_percent: int = 0,
    created_by_telegram_id: int | None,
) -> tuple[PromoCode, str | None]:
    """Создаёт промокод и пишет в Sheets.

    Returns:
        (promo, sheets_error_or_None)
    """
    name = (blogger_name or "").strip()
    if not name:
        raise ValueError("Укажи имя блогера")

    discount = parse_discount_percent(str(discount_percent))

    if code:
        normalized = normalize_code(code)
        if not is_valid_code_format(normalized):
            raise ValueError(
                "Код: 3–32 символа (латиница, цифры, _ или -)"
            )
    else:
        for _ in range(10):
            normalized = generate_code()
            existing = await session.execute(
                select(PromoCode).where(PromoCode.code == normalized)
            )
            if existing.scalar_one_or_none() is None:
                break
        else:
            raise ValueError("Не удалось сгенерировать уникальный код")

    existing = await session.execute(
        select(PromoCode).where(PromoCode.code == normalized)
    )
    if existing.scalar_one_or_none() is not None:
        raise ValueError(f"Промокод {normalized} уже существует")

    promo = PromoCode(
        code=normalized,
        blogger_name=name,
        discount_percent=discount,
        is_active=True,
        created_by_telegram_id=created_by_telegram_id,
    )
    session.add(promo)
    await session.commit()
    await session.refresh(promo)

    sheets_error: str | None = None
    try:
        from app.services import sheets as sheets_service

        await sheets_service.log_promo_code(
            code=promo.code,
            blogger=promo.blogger_name,
            active=True,
            discount_percent=promo.discount_percent,
            created_at=promo.created_at,
        )
    except Exception as exc:
        sheets_error = str(exc)
        logger.exception("Не удалось записать промокод в Sheets: %s", exc)

    logger.info(
        "Создан промокод %s для «%s» discount=%s%% by=%s sheets=%s",
        promo.code,
        promo.blogger_name,
        promo.discount_percent,
        created_by_telegram_id,
        sheets_error is None,
    )
    return promo, sheets_error


async def attach_promo_to_user(
    session: AsyncSession,
    user: User,
    code: str,
    *,
    event: str = "start",
    force: bool = False,
) -> tuple[bool, str]:
    """Привязывает промокод к пользователю.

    Returns:
        (ok, message)
    """
    promo = await get_active_promo(session, code)
    if not promo:
        return False, "Промокод не найден или неактивен."

    if user.promo_code and user.promo_code.upper() == promo.code:
        msg = f"Промокод {promo.code} уже привязан."
        if promo.discount_percent:
            msg += f" Скидка {promo.discount_percent}%."
        return True, msg

    if user.promo_code and not force:
        return (
            False,
            f"У тебя уже привязан промокод {user.promo_code}. "
            "Чтобы заменить — введи код ещё раз при оплате.",
        )

    user.promo_code = promo.code
    await session.commit()

    try:
        from app.services import sheets as sheets_service

        await sheets_service.log_attribution(
            telegram_id=user.telegram_id,
            username=user.username,
            promo=promo.code,
            event=event,
        )
    except Exception:
        logger.exception("Не удалось записать атрибуцию start в Sheets")

    settings = get_settings()
    final = price_with_discount(
        settings.subscription_price_rub, promo.discount_percent
    )
    msg = f"Промокод {promo.code} сохранён."
    if promo.discount_percent:
        msg += (
            f" Скидка {promo.discount_percent}% — "
            f"подписка {final:.0f} ₽ вместо {settings.subscription_price_rub} ₽."
        )
    else:
        msg += f" ({promo.blogger_name})"
    return True, msg


async def deactivate_promo(
    session: AsyncSession, code: str
) -> tuple[PromoCode, int]:
    """Деактивирует промокод и снимает его с пользователей.

    Returns:
        (promo, cleared_users_count)
    """
    normalized = normalize_code(code)
    result = await session.execute(
        select(PromoCode).where(PromoCode.code == normalized)
    )
    promo = result.scalar_one_or_none()
    if not promo:
        raise ValueError(f"Промокод {normalized} не найден.")
    if not promo.is_active:
        raise ValueError(f"Промокод {normalized} уже неактивен.")

    promo.is_active = False
    users = await session.execute(
        select(User).where(User.promo_code == normalized)
    )
    cleared = 0
    for u in users.scalars().all():
        u.promo_code = None
        cleared += 1
    await session.commit()
    await session.refresh(promo)

    try:
        from app.services import sheets as sheets_service

        await sheets_service.log_promo_code(
            code=promo.code,
            blogger=promo.blogger_name,
            active=False,
            discount_percent=promo.discount_percent,
            created_at=promo.created_at,
        )
    except Exception:
        logger.exception("Не удалось записать деактивацию промокода в Sheets")

    logger.info(
        "Промокод %s деактивирован, снято с пользователей: %s",
        promo.code,
        cleared,
    )
    return promo, cleared


async def list_active_promos(session: AsyncSession) -> list[PromoCode]:
    """Список активных промокодов."""
    result = await session.execute(
        select(PromoCode)
        .where(PromoCode.is_active.is_(True))
        .order_by(PromoCode.created_at.desc())
    )
    return list(result.scalars().all())
