"""Парсинг карточек Ficbook из OCR рилсов."""

from app.services.extract import (
    merge_extracted_with_dossiers,
    parse_ficbook_ocr_dossiers,
    ExtractedWork,
)

SAMPLE_OCR = """
лучшие фф, где Драко одержим Гермионой (часть 3)

Одержимый
Автор: Mary_Cherry
Фэндом: Роуллинг Джоан «Гарри Поттер»
Пэйринг и персонажи: Гермиона Грейнджер, Драко Малфой
Размер: 154 страницы, 54 294 слова, 8 частей
Дата завершения: 16.05.2013
Жанры: AU Ангст Психология

Его маленькая пташка
Автор: Pur-Pur Angel
Фэндом: Роуллинг Джоан «Гарри Поттер» Гарри Поттер (кроссовер)
Пэйринг и персонажи: Гарри Поттер/Гермиона Грейнджер, Гермиона Грейнджер/Драко Малфой
Размер: 237 страниц, 83 458 слов, 36 частей
Дата завершения: 03.11.2019
Жанры: Ангст Дарк Драма

Приютные ошибки
Автор: IsForGood
Фэндом: Роуллинг Джоан «Гарри Поттер»
Пэйринг и персонажи: Драко Малфой/Гермиона Грейнджер

Чертя его омута
Автор: kookieblackpic
Фэндом: Роуллинг Джоан «Гарри Поттер»
Пэйринг и персонажи: Драко Малфой/Гермиона Грейнджер
Размер: 8 страниц, 3 100 слов, 1 часть
Дата завершения: 19.09.2026
"""


def test_parse_ficbook_ocr_dossiers_reel():
    works = parse_ficbook_ocr_dossiers(SAMPLE_OCR)
    titles = [w.title for w in works]
    assert "Одержимый" in titles
    assert "Его маленькая пташка" in titles
    assert "Приютные ошибки" in titles
    assert "Чертя его омута" in titles
    assert "лучшие фф" not in " ".join(titles).lower()

    obsessed = next(w for w in works if w.title == "Одержимый")
    assert obsessed.author == "Mary_Cherry"
    assert obsessed.word_count == 54294

    bird = next(w for w in works if w.title == "Его маленькая пташка")
    assert bird.author == "Pur-Pur Angel"
    assert bird.word_count == 83458
    assert bird.status == "completed"
    assert bird.pairing and "Гермиона" in bird.pairing
    assert bird.from_ocr_dossier


def test_merge_adds_missed_dossier_title():
    dossiers = parse_ficbook_ocr_dossiers(SAMPLE_OCR)
    llm = [
        ExtractedWork(title="Его маленькая пташка", author="Pur-Pur Angel", confidence=0.9),
        ExtractedWork(title="Приютные ошибки", author="IsForGood", confidence=0.9),
    ]
    merged = merge_extracted_with_dossiers(llm, dossiers)
    titles = {w.title for w in merged}
    assert "Одержимый" in titles
    assert "Чертя его омута" in titles
    bird = next(w for w in merged if w.title == "Его маленькая пташка")
    assert bird.word_count == 83458


def test_merge_drops_caption_near_duplicate_and_ocr_typo():
    dossiers = parse_ficbook_ocr_dossiers(SAMPLE_OCR)
    llm = [
        ExtractedWork(title="Моя маленькая пташка", author=None, confidence=0.9),
        ExtractedWork(title="Черти его омута", author="kookieblackpic", confidence=0.9),
    ]
    merged = merge_extracted_with_dossiers(llm, dossiers)
    titles = [w.title for w in merged]
    assert "Моя маленькая пташка" not in titles
    assert "Его маленькая пташка" in titles
    # OCR-канон, не опечатка LLM
    assert "Чертя его омута" in titles
    assert "Черти его омута" not in titles
