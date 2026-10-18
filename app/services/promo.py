"""Промокоды блогеров."""

from __future__ import annotations

import logging
import re
import secrets
import string

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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


async def create_promo(
    session: AsyncSession,
    *,
    blogger_name: str,
    code: str | None,
    created_by_telegram_id: int | None,
) -> PromoCode:
    """Создаёт промокод и пишет в Sheets."""
    name = (blogger_name or "").strip()
    if not name:
        raise ValueError("Укажи имя блогера")

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
        is_active=True,
        created_by_telegram_id=created_by_telegram_id,
    )
    session.add(promo)
    await session.commit()
    await session.refresh(promo)

    try:
        from app.services import sheets as sheets_service

        await sheets_service.log_promo_code(
            code=promo.code,
            blogger=promo.blogger_name,
            active=True,
            created_at=promo.created_at,
        )
    except Exception:
        logger.exception("Не удалось записать промокод в Sheets")

    logger.info(
        "Создан промокод %s для «%s» by=%s",
        promo.code,
        promo.blogger_name,
        created_by_telegram_id,
    )
    return promo


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
        return True, f"Промокод {promo.code} уже привязан."

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

    return True, f"Промокод {promo.code} ({promo.blogger_name}) сохранён. Спасибо!"
