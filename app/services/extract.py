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
    # Метаданные с карточки Ficbook/AO3 в OCR (если распознаны)
    fandom: str | None = None
    pairing: str | None = None
    word_count: int | None = None
    status: str | None = None
    tags: list[str] | None = None
    from_ocr_dossier: bool = False

    @property
    def has_dossier_meta(self) -> bool:
        return bool(self.fandom or self.pairing or self.word_count or self.tags)


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
    # Не «Одержимый» (название фика) — только фразы подборки
    "одержим гермион",
    "одержим драко",
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

    Только явный @handle. Ники вроде Mary_Cherry / SenLinYu на Ficbook не трогаем.
    """
    if not author:
        return False
    cleaned = author.strip()
    if not cleaned.startswith("@"):
        return False
    if " " in cleaned:
        return False
    return bool(_HANDLE_AUTHOR_RE.match(cleaned.lstrip("@")))


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
        "Если в OCR карточки Ficbook (строки «Автор:», «Фэндом:», «Размер:») — "
        "название обычно строка ПЕРЕД «Автор:»; извлеки ВСЕ такие карточки. "
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


_DOSSIER_FIELD_RE = re.compile(
    r"^(Автор|Фэндом|Вселенная|Пэйринг и персонажи|Пэйринг|Размер|Жанры|"
    r"Предупреждения|Отношения|Дата завершения|Дата обновления)\s*:\s*(.*)$",
    re.I,
)
_WORDS_RE = re.compile(r"([\d\s\u00a0]+)\s*слов", re.I)
_PAIRING_SLASH_RE = re.compile(
    r"([A-Za-zА-Яа-яЁё][^,/]{1,40})\s*/\s*([A-Za-zА-Яа-яЁё][^,/]{1,40})"
)


def _parse_word_count(size_line: str) -> int | None:
    m = _WORDS_RE.search(size_line or "")
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(1))
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _parse_pairing(line: str) -> str | None:
    """Берёт первую пару A/B из строки пэйрингов."""
    m = _PAIRING_SLASH_RE.search(line or "")
    if not m:
        return None
    return f"{m.group(1).strip()}/{m.group(2).strip()}"


def parse_ficbook_ocr_dossiers(ocr_text: str) -> list[ExtractedWork]:
    """Парсит карточки Ficbook из OCR (Автор:/Фэндом:/Размер:…)."""
    if not ocr_text or "Автор:" not in ocr_text:
        return []

    lines = [ln.strip() for ln in ocr_text.splitlines()]
    blocks: list[list[str]] = []
    current: list[str] = []
    for ln in lines:
        if not ln:
            if current:
                blocks.append(current)
                current = []
            continue
        current.append(ln)
    if current:
        blocks.append(current)

    results: list[ExtractedWork] = []
    for block in blocks:
        # Карточка: строка-название + хотя бы одно поле «Ключ:»
        field_idxs = [i for i, ln in enumerate(block) if _DOSSIER_FIELD_RE.match(ln)]
        if not field_idxs:
            continue
        first_field = field_idxs[0]
        if first_field == 0:
            continue
        title = block[first_field - 1].strip()
        # Иногда перед названием есть заголовок подборки в том же блоке
        if not _is_valid_title(title):
            # ищем ближайшую валидную строку выше первого поля
            title = ""
            for i in range(first_field - 1, -1, -1):
                if _is_valid_title(block[i]) and not _DOSSIER_FIELD_RE.match(block[i]):
                    title = block[i]
                    break
        if not title or not _is_valid_title(title):
            continue

        fields: dict[str, str] = {}
        for ln in block[first_field:]:
            m = _DOSSIER_FIELD_RE.match(ln)
            if m:
                fields[m.group(1).casefold()] = m.group(2).strip()

        # Автор с карточки Ficbook — не фильтруем как IG handle (Mary_Cherry и т.п.)
        author = fields.get("автор") or None
        if author and author.startswith("@"):
            author = author.lstrip("@") or None
        fandom = fields.get("фэндом") or fields.get("вселенная") or None
        pairing = _parse_pairing(fields.get("пэйринг и персонажи") or fields.get("пэйринг") or "")
        word_count = _parse_word_count(fields.get("размер") or "")
        tags_raw = fields.get("жанры") or ""
        tags = [t for t in re.split(r"\s{2,}|\s*,\s*", tags_raw) if t] if tags_raw else None
        if tags:
            # «AU Ангст Драма» без запятых — режем по пробелам, если одна «фраза»
            if len(tags) == 1 and " " in tags[0]:
                tags = tags[0].split()

        status = None
        if fields.get("дата завершения"):
            status = "completed"
        elif fields.get("дата обновления") or "планируется" in (fields.get("размер") or "").lower():
            status = "in_progress"

        results.append(
            ExtractedWork(
                title=title,
                author=author,
                confidence=0.95,
                fandom=fandom,
                pairing=pairing,
                word_count=word_count,
                status=status,
                tags=tags or None,
                from_ocr_dossier=True,
            )
        )

    logger.info("OCR-досье Ficbook: %s карточек", len(results))
    return results


def _titles_near_duplicate(a: str, b: str, *, threshold: float = 88.0) -> bool:
    """«Чертя/Черти», «Его/Моя маленькая пташка» — достаточно близко для merge."""
    from rapidfuzz import fuzz

    na, nb = normalize_loose(a), normalize_loose(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    return float(fuzz.token_set_ratio(na, nb)) >= threshold and float(
        fuzz.partial_ratio(na, nb)
    ) >= threshold


def _merge_meta_into(existing: ExtractedWork, item: ExtractedWork, *, prefer_dossier: bool) -> None:
    if item.author and (not existing.author or prefer_dossier):
        if prefer_dossier or not existing.author:
            existing.author = item.author or existing.author
    if item.fandom and (prefer_dossier or not existing.fandom):
        existing.fandom = item.fandom
    if item.pairing and (prefer_dossier or not existing.pairing):
        existing.pairing = item.pairing
    if item.word_count and (prefer_dossier or not existing.word_count):
        existing.word_count = item.word_count
    if item.status and (prefer_dossier or not existing.status):
        existing.status = item.status
    if item.tags and (prefer_dossier or not existing.tags):
        existing.tags = item.tags
    if item.from_ocr_dossier:
        existing.from_ocr_dossier = True
    existing.confidence = max(existing.confidence, item.confidence)


def merge_extracted_with_dossiers(
    llm_works: list[ExtractedWork],
    dossiers: list[ExtractedWork],
) -> list[ExtractedWork]:
    """Досье OCR — источник метаданных; LLM дополняет пропущенные названия."""
    if not dossiers:
        return llm_works

    merged: list[ExtractedWork] = []

    def _find_near(title: str) -> ExtractedWork | None:
        for existing in merged:
            if _titles_near_duplicate(existing.title, title):
                return existing
        return None

    for d in dossiers:
        hit = _find_near(d.title)
        if hit is None:
            merged.append(d)
        else:
            _merge_meta_into(hit, d, prefer_dossier=True)
            # Каноническое название — с карточки OCR
            if d.from_ocr_dossier:
                hit.title = d.title

    for w in llm_works:
        hit = _find_near(w.title)
        if hit is None:
            # Caption «Моя …» при OCR «Его …» — не плодим дубль без досье
            if not w.from_ocr_dossier and not w.has_dossier_meta:
                near_dossier = next(
                    (
                        m
                        for m in merged
                        if m.from_ocr_dossier and _titles_near_duplicate(m.title, w.title)
                    ),
                    None,
                )
                if near_dossier is not None:
                    logger.info(
                        "Пропуск caption/LLM дубля «%s» — рядом OCR «%s»",
                        w.title,
                        near_dossier.title,
                    )
                    if w.author and not near_dossier.author:
                        near_dossier.author = w.author
                    continue
            merged.append(w)
        else:
            _merge_meta_into(hit, w, prefer_dossier=False)

    return merged


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
    Карточки Ficbook в OCR парсятся отдельно и обогащают результат.
    """
    dossiers = parse_ficbook_ocr_dossiers(ocr_text or "")

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
            if dossiers:
                return scrub_authors_that_are_titles(dossiers)
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
            return scrub_authors_that_are_titles(dossiers)
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

    if dossiers:
        result = merge_extracted_with_dossiers(result, dossiers)
    elif not result and from_media:
        result = dossiers
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
