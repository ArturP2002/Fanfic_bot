"""Запись промокодов и атрибуций в Google Sheets."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)

# Листы и колонки на русском
SHEET_PROMO = "Промокоды"
SHEET_ATTR = "Атрибуции"

_PROMO_HEADER = ["код", "блогер", "скидка_%", "активен", "создан"]
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
_spreadsheet_cache = None


class SheetsError(Exception):
    """Ошибка работы с Google Sheets."""


def _credentials_path() -> Path | None:
    """Путь к JSON service account (относительный — от корня проекта / cwd)."""
    settings = get_settings()
    raw = (settings.google_service_account_json or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute() and path.is_file():
        return path
    candidates = [
        _PROJECT_ROOT / path,
        Path.cwd() / path,
        Path("/app") / path,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    # вернём ожидаемый путь для диагностики
    return (_PROJECT_ROOT / path).resolve()


def sheets_configured() -> bool:
    settings = get_settings()
    path = _credentials_path()
    return bool(
        path
        and path.is_file()
        and (settings.google_sheets_spreadsheet_id or "").strip()
    )


def sheets_status() -> str:
    """Краткий статус настройки для админа."""
    settings = get_settings()
    path = _credentials_path()
    sid = (settings.google_sheets_spreadsheet_id or "").strip()
    lines = []
    if not path:
        lines.append("JSON: не задан GOOGLE_SERVICE_ACCOUNT_JSON")
    elif not path.is_file():
        lines.append(f"JSON: файл не найден ({path})")
    else:
        lines.append(f"JSON: ok ({path})")
    lines.append(f"Таблица: {'ok' if sid else 'не задан GOOGLE_SHEETS_SPREADSHEET_ID'}")
    if sid:
        lines.append(f"ID: {sid}")
    return "\n".join(lines)


def _get_spreadsheet(force: bool = False):
    """Открывает таблицу через service account."""
    global _spreadsheet_cache
    if _spreadsheet_cache is not None and not force:
        return _spreadsheet_cache

    import gspread
    from google.oauth2.service_account import Credentials

    settings = get_settings()
    path = _credentials_path()
    if path is None or not path.is_file():
        raise SheetsError(
            f"Файл credentials не найден: {path}. "
            "Проверь GOOGLE_SERVICE_ACCOUNT_JSON и что файл есть на сервере."
        )
    sid = (settings.google_sheets_spreadsheet_id or "").strip()
    if not sid:
        raise SheetsError("Не задан GOOGLE_SHEETS_SPREADSHEET_ID в .env")

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    try:
        creds = Credentials.from_service_account_file(str(path), scopes=scopes)
        client = gspread.Client(auth=creds)
        ss = client.open_by_key(sid)
    except Exception as exc:
        _spreadsheet_cache = None
        raise SheetsError(f"Не удалось открыть таблицу: {exc}") from exc

    _spreadsheet_cache = ss
    return ss


def _ensure_worksheet(title: str, header: list[str]):
    ss = _get_spreadsheet()
    try:
        ws = ss.worksheet(title)
    except Exception:
        logger.info("Создаю лист Google Sheets «%s»", title)
        ws = ss.add_worksheet(title=title, rows=1000, cols=max(len(header), 8))
        ws.append_row(header, value_input_option="USER_ENTERED")
        return ws
    values = ws.get_all_values()
    if not values:
        ws.append_row(header, value_input_option="USER_ENTERED")
    return ws


def _append_row_sync(sheet_title: str, header: list[str], row: list[Any]) -> None:
    if not sheets_configured():
        raise SheetsError(
            "Google Sheets не настроены. "
            + sheets_status()
        )
    try:
        ws = _ensure_worksheet(sheet_title, header)
        ws.append_row(
            [str(c) if c is not None else "" for c in row],
            value_input_option="USER_ENTERED",
        )
        logger.info(
            "Sheets: записано в «%s»: %s",
            sheet_title,
            row,
        )
    except SheetsError:
        raise
    except Exception as exc:
        global _spreadsheet_cache
        _spreadsheet_cache = None
        logger.exception("Sheets: ошибка записи в «%s»", sheet_title)
        raise SheetsError(f"Ошибка записи в «{sheet_title}»: {exc}") from exc


async def log_promo_code(
    *,
    code: str,
    blogger: str,
    active: bool,
    discount_percent: int = 0,
    created_at: datetime | None = None,
) -> None:
    """Добавляет строку в лист «Промокоды». Бросает SheetsError при сбое."""
    ts = (created_at or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    await asyncio.to_thread(
        _append_row_sync,
        SHEET_PROMO,
        _PROMO_HEADER,
        [code, blogger, discount_percent, "да" if active else "нет", ts],
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
    """Добавляет строку в лист «Атрибуции». Бросает SheetsError при сбое."""
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


async def ping_sheets() -> str:
    """Проверка доступа: создаёт/открывает листы и пишет тестовую строку."""
    if not sheets_configured():
        raise SheetsError("Не настроено.\n" + sheets_status())
    await asyncio.to_thread(
        _append_row_sync,
        SHEET_PROMO,
        _PROMO_HEADER,
        ["__PING__", "проверка", 0, "да", datetime.now(timezone.utc).isoformat(timespec="seconds")],
    )
    return "Связь с Google Sheets ок. Лист «Промокоды» доступен для записи."
