"""Запись промокодов и атрибуций в Google Sheets."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)

# Листы и колонки на русском
SHEET_PROMO = "Промокоды"
SHEET_ATTR = "Атрибуции"

_PROMO_HEADER = ["код", "блогер", "активен", "создан"]
_ATTR_HEADER = [
    "telegram_id",
    "username",
    "промокод",
    "событие",
    "оплачен",
    "сумма",
    "id_платежа",
    "тест",
]

_EVENT_RU = {
    "start": "старт",
    "subscribe": "подписка",
    "subscribe_promo": "промокод_при_оплате",
}

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _credentials_path() -> Path | None:
    """Путь к JSON service account (относительный — от корня проекта)."""
    settings = get_settings()
    raw = (settings.google_service_account_json or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    return path


def sheets_configured() -> bool:
    settings = get_settings()
    path = _credentials_path()
    return bool(
        path
        and path.is_file()
        and settings.google_sheets_spreadsheet_id.strip()
    )


@lru_cache
def _get_spreadsheet():
    """Открывает таблицу через service account (кэш клиента)."""
    import gspread
    from google.oauth2.service_account import Credentials

    settings = get_settings()
    path = _credentials_path()
    if path is None:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON не задан")

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(str(path), scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(settings.google_sheets_spreadsheet_id)


def _ensure_worksheet(title: str, header: list[str]):
    ss = _get_spreadsheet()
    try:
        ws = ss.worksheet(title)
    except Exception:
        ws = ss.add_worksheet(title=title, rows=1000, cols=len(header))
        ws.append_row(header, value_input_option="USER_ENTERED")
        return ws
    values = ws.get_all_values()
    if not values:
        ws.append_row(header, value_input_option="USER_ENTERED")
    return ws


def _append_row_sync(sheet_title: str, header: list[str], row: list[Any]) -> None:
    if not sheets_configured():
        logger.warning(
            "Google Sheets не настроены — пропуск записи в %s: %s",
            sheet_title,
            row,
        )
        return
    ws = _ensure_worksheet(sheet_title, header)
    ws.append_row(
        [str(c) if c is not None else "" for c in row],
        value_input_option="USER_ENTERED",
    )


async def log_promo_code(
    *,
    code: str,
    blogger: str,
    active: bool,
    created_at: datetime | None = None,
) -> None:
    """Добавляет строку в лист «Промокоды»."""
    ts = (created_at or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    await asyncio.to_thread(
        _append_row_sync,
        SHEET_PROMO,
        _PROMO_HEADER,
        [code, blogger, "да" if active else "нет", ts],
    )


async def log_attribution(
    *,
    telegram_id: int,
    username: str | None,
    promo: str | None,
    event: str,
    paid_at: datetime | None = None,
    amount: float | None = None,
    payment_id: str | None = None,
    is_test: bool = False,
) -> None:
    """Добавляет строку в лист «Атрибуции»."""
    paid_str = paid_at.isoformat(timespec="seconds") if paid_at else ""
    event_ru = _EVENT_RU.get(event, event)
    await asyncio.to_thread(
        _append_row_sync,
        SHEET_ATTR,
        _ATTR_HEADER,
        [
            telegram_id,
            username or "",
            promo or "",
            event_ru,
            paid_str,
            amount if amount is not None else "",
            payment_id or "",
            "да" if is_test else "нет",
        ],
    )
