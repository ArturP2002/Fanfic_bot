"""Извлечение названий фанфиков из текста / OCR / транскрипта."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass

from openai import RateLimitError as OpenAIRateLimitError

from app.config import get_settings
from app.proxy import openai_client
from app.services.media.errors import RateLimitError

logger = logging.getLogger(__name__)


@dataclass
class ExtractedWork:
    """Одно извлечённое упоминание произведения."""

    title: str
    author: str | None = None
    confidence: float = 1.0  # 0..1


_URL_IN_TITLE_RE = re.compile(
    r"https?://|www\.|instagram\.com|tiktok\.com|ficbook\.net|archiveofourown\.org",
    re.I,
)
_BAD_TITLE_MARKERS = (
    "текст пользователя",
    "транскрипт речи",
    "текст с изображений",
    "user text",
)
# Темы подборок / заголовки роликов, не названия отдельных фиков
_LISTICLE_JUNK_MARKERS = (
    "лучшие фф",
    "лучшие фанфики",
    "лучшие фики",
    "лучшие фиксы",
    "топ фф",
    "топ 10",
    "top 10",
    "подборка",
    "рекомендац",
    "где драко",
    "где гермион",
    "фф где",
    "фики где",
    "фанфики где",
    "одержим",
)
# Типичные речевые обрывки, которые эвристика ошибочно принимает за названия
_SPEECH_JUNK_MARKERS = (
    "потому что",
    "я должна",
    "я должен",
    "номер ",
    " будет",
    "сейчас я",
    "давайте",
    "смотрите",
    "итак",
    "ну вот",
    "в общем",
    "короче",
    "типа того",
    "мне кажется",
    "я думаю",
    "я хочу",
    "хочу сказать",
    "следующий",
    "первый это",
    "второй это",
    "третий это",
    "мне нравится",
    "это меня",
    "вы должны",
    "вы можете",
    "я недавно",
    "сделала видео",
    "делаю мою",
    "этот фикс",
    "этот фильм",
    "эти фиксы",
    "фиксов",
    "тоже рекомендую",
)
# «где X …» без кавычек — описание темы, не title
_WHERE_PAIRING_RE = re.compile(
    r"\bгде\b.+\b(драко|гермион|гарри|северус|снейп|том|грейнджер)\b",
    re.I,
)
_HANDLE_AUTHOR_RE = re.compile(r"^@?[\w.]{3,30}$")
# Названия редко начинаются с союза/местоимения разговорной речи
_SPEECH_STARTS = (
    "и ",
    "а ",
    "но ",
    "что ",
    "как ",
    "когда ",
    "который ",
    "которая ",
    "которое ",
    "которые ",
    "чтобы ",
    "если ",
    "поэтому ",
    "просто ",
    "особенно ",
    "прежде ",
    "рамки ",
    "давайте ",
    "смотрите ",
    "okay",
    "окей",
)

_MAX_TITLE_CHARS = 80
_MAX_TITLE_WORDS = 8
# Короткий ввод вроде «MOAM» / «Lionheart» — сначала ищем в каталоге, без LLM
_SHORT_QUERY_MAX_CHARS = 60
_SHORT_QUERY_MAX_WORDS = 8


def is_short_catalog_query(text: str) -> bool:
    """True, если весь ввод похож на одно название или сокращение."""
    cleaned = (text or "").strip()
    if not cleaned or _URL_IN_TITLE_RE.search(cleaned):
        return False
    if "\n" in cleaned:
        return False
    if len(cleaned) > _SHORT_QUERY_MAX_CHARS:
        return False
    if len(cleaned.split()) > _SHORT_QUERY_MAX_WORDS:
        return False
    return _is_valid_title(cleaned)


def is_junk_title(title: str) -> bool:
    """True, если title похож на обрывок речи / мусор, а не на название."""
    cleaned = (title or "").strip()
    if len(cleaned) > _MAX_TITLE_CHARS:
        return True
    if len(cleaned.split()) > _MAX_TITLE_WORDS:
        return True
    lower = cleaned.lower()
    if any(marker in lower for marker in _SPEECH_JUNK_MARKERS):
        return True
    if any(marker in lower for marker in _LISTICLE_JUNK_MARKERS):
        return True
    if _WHERE_PAIRING_RE.search(cleaned):
        return True
    if any(lower.startswith(start) for start in _SPEECH_STARTS):
        return True
    if any(marker in lower for marker in _BAD_TITLE_MARKERS):
        return True
    if _URL_IN_TITLE_RE.search(cleaned):
        return True
    # Несколько предложений / точки внутри — почти наверняка речь
    if cleaned.count(".") >= 1 and len(cleaned) > 40:
        return True
    return False


def is_social_handle(author: str | None) -> bool:
    """True, если author похож на Instagram/TikTok username, а не на имя автора фика.

    Не трогаем обычные ники авторов (SenLinYu): только @handle, snake_case / dots.
    """
    if not author:
        return False
    cleaned = author.strip()
    if " " in cleaned:
        return False
    if any("\u0400" <= ch <= "\u04FF" for ch in cleaned):
        return False
    if cleaned.startswith("@"):
        return True
    if "_" in cleaned or "." in cleaned:
        return bool(_HANDLE_AUTHOR_RE.match(cleaned.lstrip("@")))
    return False


def _is_valid_title(title: str) -> bool:
    """Отсекает мусор: ссылки, служебные фразы, слишком длинный текст, речь."""
    cleaned = (title or "").strip()
    if len(cleaned) < 2:
        return False
    if is_junk_title(cleaned):
        return False
    # Одна голая ссылка / путь
    if cleaned.startswith("/") or (" " not in cleaned and "." in cleaned and "/" in cleaned):
        return False
    return True


def extract_from_list_heuristic(text: str) -> list[ExtractedWork] | None:
    """Эвристика только для явного списка названий (короткий пользовательский текст)."""
    lines = [line.strip(" -•*\t") for line in text.splitlines()]
    lines = [line for line in lines if line]
    if len(lines) < 2:
        return None
    # Если большинство строк короткие и без точек в конце — считаем списком
    short = sum(1 for line in lines if len(line) <= 80 and not line.endswith("."))
    if short / len(lines) < 0.7:
        return None
    cleaned = []
    for line in lines:
        lower = line.lower()
        if lower.startswith(("добавь", "add ", "список")):
            continue
        if not _is_valid_title(line):
            continue
        cleaned.append(ExtractedWork(title=line, confidence=0.9))
    return cleaned or None


def _is_rate_limit_error(exc: BaseException) -> bool:
    if isinstance(exc, (OpenAIRateLimitError, RateLimitError)):
        return True
    text = str(exc).lower()
    return "rate limit" in text or "429" in text or "tokens per min" in text


async def _llm_extract(
    combined: str, *, from_media: bool = False
) -> list[ExtractedWork]:
    """Вызов LLM с retry на 429."""
    settings = get_settings()
    client = openai_client()
    min_confidence = 0.5 if from_media else 0.35
    system = (
        "Ты помощник FicBrain. Из текста рекомендаций фанфиков извлеки все "
        "упомянутые произведения. Верни строго JSON-массив объектов "
        '{"title": str, "author": str|null, "confidence": float}. '
        "Не выдумывай названия и авторов. Если уверенность низкая — "
        "confidence < 0.5. Если автор неизвестен — null. "
        "Один объект = одно произведение. Язык названий сохраняй как в источнике. "
        "Не считай URL, ссылки Instagram/TikTok и служебные фразы названиями фанфиков. "
        "Не бери обрывки речи («потому что», «я должна», «номер 10 будет») как названия. "
        "Не бери темы подборок и заголовки роликов "
        "(«лучшие фф где…», «топ 10», «подборка», «рекомендации») — это не названия. "
        "Не используй Instagram/TikTok username (@handle) как author фанфика. "
        "Не ставь название другого фанфика из того же списка в поле author. "
        "Название обычно короткое: до 8 слов. "
        "Сокращения и аббревиатуры (MOAM, DMATMOOBIL, LH) — валидные названия, "
        "если весь ввод или пункт списка ими является; не отбрасывай их. "
        "В описании поста Instagram/TikTok тоже ищи названия фанфиков. "
        "Если на экране/в OCR нумерованный или маркированный список — "
        "извлеки ВСЕ пункты-названия, не пропускай. "
        "Если произведений нет — верни {\"works\": []}."
    )
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                "Извлеки фанфики и верни JSON вида "
                '{"works":[{"title":"...","author":null,"confidence":0.9}]}.\n\n'
                + combined
            ),
        },
    ]

    delays = (1.0, 2.0, 4.0)
    last_exc: BaseException | None = None
    for attempt, delay in enumerate((0.0, *delays)):
        if delay:
            logger.warning(
                "LLM rate limit — повтор через %.0f с (попытка %s)",
                delay,
                attempt + 1,
            )
            await asyncio.sleep(delay)
        try:
            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0.1,
                response_format={"type": "json_object"},
                messages=messages,
            )
            content = response.choices[0].message.content or "{}"
            data = json.loads(content)
            items = data.get("works") if isinstance(data, dict) else data
            result: list[ExtractedWork] = []
            for item in items or []:
                title = (item.get("title") or "").strip()
                if not _is_valid_title(title):
                    logger.info("Пропуск мусорного извлечения: %s", title)
                    continue
                conf = float(item.get("confidence") or 0.5)
                if conf < min_confidence:
                    logger.info("Пропуск неуверенного извлечения: %s", title)
                    continue
                author = item.get("author")
                if author:
                    author = str(author).strip() or None
                if is_social_handle(author):
                    logger.info("Сброс IG/TikTok handle как author: %s", author)
                    author = None
                result.append(ExtractedWork(title=title, author=author, confidence=conf))
            logger.info("LLM извлёк %s произведений", len(result))
            return result
        except Exception as exc:
            last_exc = exc
            if _is_rate_limit_error(exc) and attempt < len(delays):
                continue
            if _is_rate_limit_error(exc):
                raise RateLimitError(
                    "Сервис распознавания перегружен (LLM rate limit)"
                ) from exc
            raise

    if last_exc and _is_rate_limit_error(last_exc):
        raise RateLimitError(
            "Сервис распознавания перегружен (LLM rate limit)"
        ) from last_exc
    if last_exc:
        raise last_exc
    return []


def _text_fallback(text: str, heuristic: list[ExtractedWork] | None) -> list[ExtractedWork]:
    """Эвристика списка или простое извлечение для короткого пользовательского текста."""
    if heuristic:
        return heuristic
    return _simple_named_extract(text or "")


def scrub_authors_that_are_titles(works: list[ExtractedWork]) -> list[ExtractedWork]:
    """Сбрасывает author, если он совпадает с другим title в том же списке."""
    titles = {normalize_loose(w.title) for w in works if w.title}
    for work in works:
        if work.author and normalize_loose(work.author) in titles:
            logger.info(
                "Сброс author=«%s» — это другое название из списка",
                work.author,
            )
            work.author = None
    return works


def normalize_loose(value: str) -> str:
    """Грубая нормализация для сравнения title/author внутри batch."""
    return " ".join((value or "").casefold().split())


async def extract_works_with_llm(
    text: str,
    *,
    ocr_text: str | None = None,
    transcript: str | None = None,
    caption: str | None = None,
) -> list[ExtractedWork]:
    """Извлекает произведения через LLM.

    Эвристика списков — только для короткого пользовательского текста без
    транскрипта/OCR. По медиа при сбое LLM возвращаем [] (не режем речь на «названия»).
    Описание поста (caption) учитывается вместе с речью и OCR.
    """
    combined_parts = []
    if caption:
        combined_parts.append(f"Описание поста Instagram/TikTok:\n{caption}")
    if text:
        combined_parts.append(f"Текст пользователя:\n{text}")
    if transcript:
        combined_parts.append(f"Транскрипт речи:\n{transcript}")
    if ocr_text:
        combined_parts.append(f"Текст с изображений (OCR):\n{ocr_text}")
    combined = "\n\n".join(combined_parts).strip()

    from_media = bool(transcript or ocr_text or caption)
    # Эвристику списка — только для явного пользовательского текста без медиа
    heuristic = None if from_media else extract_from_list_heuristic(text or "")

    settings = get_settings()
    if not settings.openai_api_key:
        logger.warning("OPENAI_API_KEY не задан — используется эвристическое извлечение")
        if from_media:
            # Без LLM по медиа берём только caption как текст, если он есть
            if caption and not transcript and not ocr_text:
                return scrub_authors_that_are_titles(_simple_named_extract(caption))
            return []
        return scrub_authors_that_are_titles(_text_fallback(text or "", heuristic))

    try:
        result = await _llm_extract(combined, from_media=from_media)
    except RateLimitError:
        raise
    except Exception:
        logger.exception("Ошибка LLM-извлечения")
        if from_media:
            # Не режем транскрипт на «фанфики» — лучше пустой результат
            return []
        return scrub_authors_that_are_titles(_text_fallback(text or "", heuristic))

    # LLM вернул пусто на обычном тексте — пробуем эвристику (MOAM и т.п.)
    if not result and not from_media:
        fallback = _text_fallback(text or "", heuristic)
        if fallback:
            logger.info(
                "LLM пусто — эвристический fallback: %s",
                [item.title for item in fallback],
            )
        return scrub_authors_that_are_titles(fallback)
    return scrub_authors_that_are_titles(result)


def _simple_named_extract(text: str) -> list[ExtractedWork]:
    """Простое извлечение после «добавь» / перечисления через запятую и «и»."""
    cleaned = text.strip()
    cleaned = re.sub(
        r"^(добавь|add|хочу прочитать|мне посоветовали)\s+",
        "",
        cleaned,
        flags=re.I,
    )
    cleaned = re.sub(r",?\s*хочу потом прочитать\.?$", "", cleaned, flags=re.I)
    parts = re.split(r"\s+и\s+|,\s*", cleaned)
    result = []
    for part in parts:
        part = part.strip(" .")
        if not _is_valid_title(part):
            continue
        if part.lower() in {"мне посоветовали", "потом", "прочитать"}:
            continue
        result.append(ExtractedWork(title=part, confidence=0.7))
    logger.info("Эвристика извлекла %s произведений", len(result))
    return result
