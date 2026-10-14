"""Нормализация названий и авторов для сопоставления."""

import re
import unicodedata


_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")
_BILINGUAL_SEP_RE = re.compile(r"\s*[/|]\s*")
_CYRILLIC_RE = re.compile(r"[а-яё]", re.I)
_LATIN_RE = re.compile(r"[a-z]", re.I)


def normalize_text(value: str | None) -> str:
    """Приводит строку к виду, удобному для сравнения.

    Lowercase, ё→е, удаление пунктуации и лишних пробелов.
    """
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value).strip().lower()
    text = text.replace("ё", "е")
    text = _PUNCT_RE.sub(" ", text)
    text = _SPACE_RE.sub(" ", text).strip()
    return text


def _script(text: str) -> str | None:
    """Преобладающий алфавит строки: cyrillic / latin / None."""
    cyr = len(_CYRILLIC_RE.findall(text))
    lat = len(_LATIN_RE.findall(text))
    if cyr > lat:
        return "cyrillic"
    if lat > cyr:
        return "latin"
    return None


def split_bilingual_title(title: str | None) -> tuple[str, str] | None:
    """Делит «Мера Человека/Measure Of A Man» на (русское, оригинальное).

    Срабатывает только на две части с разными алфавитами, чтобы не резать
    названия вроде «Правильное решение/Неправильные вещи».
    """
    if not title:
        return None
    parts = [part.strip() for part in _BILINGUAL_SEP_RE.split(title) if part.strip()]
    if len(parts) != 2:
        return None
    first, second = parts
    scripts = (_script(first), _script(second))
    if scripts == ("cyrillic", "latin"):
        return first, second
    if scripts == ("latin", "cyrillic"):
        return second, first
    return None


def split_aliases(raw: str | None) -> list[str]:
    """Разбивает поле «другие названия / сокращения» по разделителям."""
    if not raw or not str(raw).strip():
        return []
    parts = re.split(r"[;|,/\n]+", str(raw))
    return [part.strip() for part in parts if part.strip()]
