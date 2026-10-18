"""Проверка доступа: подписка, пробные использования, админы."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import User

logger = logging.getLogger(__name__)


@dataclass
class AccessResult:
    """Итог проверки доступа к платной обработке."""

    allowed: bool
    reason: str  # ok | admin | subscription | trial | paywall
    message: str | None = None
    consume_trial: bool = False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def has_active_subscription(user: User) -> bool:
    """Есть ли действующая подписка."""
    until = user.subscription_until
    if until is None:
        return False
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until > _utcnow()


def ensure_free_uses_initialized(user: User) -> None:
    """Выставляет free_uses_left из env при первом обращении."""
    if user.free_uses_left is not None:
        return
    settings = get_settings()
    user.free_uses_left = max(0, int(settings.free_trial_uses))


def check_processing_access(user: User, telegram_id: int) -> AccessResult:
    """Проверяет, можно ли запускать тяжёлую обработку (видео/скрины/LLM)."""
    settings = get_settings()

    if telegram_id in settings.admin_ids:
        return AccessResult(allowed=True, reason="admin")

    if has_active_subscription(user):
        return AccessResult(allowed=True, reason="subscription")

    ensure_free_uses_initialized(user)

    if settings.free_trial_uses <= 0:
        return AccessResult(
            allowed=False,
            reason="paywall",
            message=_paywall_message(trial_exhausted=False),
        )

    left = user.free_uses_left if user.free_uses_left is not None else 0
    if left > 0:
        return AccessResult(
            allowed=True,
            reason="trial",
            consume_trial=True,
            message=None,
        )

    return AccessResult(
        allowed=False,
        reason="paywall",
        message=_paywall_message(trial_exhausted=True),
    )


def _paywall_message(*, trial_exhausted: bool) -> str:
    settings = get_settings()
    price = settings.subscription_price_rub
    if trial_exhausted:
        return (
            "Бесплатные разборы закончились.\n\n"
            f"Оформи годовую подписку за {price} ₽ — кнопка «💎 Подписка» "
            "или команда /subscribe."
        )
    return (
        "Для разбора роликов и скриншотов нужна подписка.\n\n"
        f"Год доступа — {price} ₽. Нажми «💎 Подписка» или /subscribe."
    )


async def consume_trial_use(session: AsyncSession, user: User) -> int:
    """Списывает одно пробное использование. Возвращает остаток."""
    ensure_free_uses_initialized(user)
    left = user.free_uses_left if user.free_uses_left is not None else 0
    if left > 0:
        user.free_uses_left = left - 1
        await session.commit()
        logger.info(
            "Списано пробное использование user_id=%s осталось=%s",
            user.id,
            user.free_uses_left,
        )
    return user.free_uses_left or 0


def subscription_status_text(user: User, telegram_id: int) -> str:
    """Текст статуса подписки для UI."""
    settings = get_settings()
    if telegram_id in settings.admin_ids:
        return "Статус: админ (полный доступ)."

    if has_active_subscription(user):
        until = user.subscription_until
        assert until is not None
        local = until.astimezone(timezone.utc).strftime("%d.%m.%Y")
        return f"Подписка активна до {local} (UTC)."

    ensure_free_uses_initialized(user)
    if settings.free_trial_uses <= 0:
        return "Подписки нет. Бесплатных разборов не предусмотрено."

    left = user.free_uses_left if user.free_uses_left is not None else 0
    return f"Подписки нет. Осталось бесплатных разборов: {left}."
