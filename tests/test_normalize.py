"""Тесты нормализации и эвристического извлечения."""

from app.services.extract import (
    _is_valid_title,
    _simple_named_extract,
    extract_from_list_heuristic,
)
from app.services.normalize import normalize_text, split_aliases, split_bilingual_title


def test_split_bilingual_title():
    assert split_bilingual_title("Мера Человека/Measure Of A Man") == (
        "Мера Человека",
        "Measure Of A Man",
    )
    assert split_bilingual_title("Remain Nameless | Оставайся безымянным") == (
        "Оставайся безымянным",
        "Remain Nameless",
    )
    assert split_bilingual_title("EXIT / ВЫХОД") == ("ВЫХОД", "EXIT")


def test_split_bilingual_title_keeps_same_language_titles():
    assert split_bilingual_title("Правильное решение/Неправильные вещи") is None
    assert split_bilingual_title("Мятные Конфеты / Боевые Шрамы") is None
    assert split_bilingual_title("Номура") is None


def test_normalize_yo_and_punct():
    assert normalize_text("  Львиное  Сердце! ") == "львиное сердце"
    assert normalize_text("Ёжик") == "ежик"


def test_split_aliases():
    assert split_aliases("DMATMOOBIL; BATMOBILE") == ["DMATMOOBIL", "BATMOBILE"]


def test_list_heuristic():
    text = "Manacled\nThe Fallout\nLionheart\nMeasure of a Man"
    items = extract_from_list_heuristic(text)
    assert items is not None
    assert len(items) == 4


def test_simple_named_extract():
    items = _simple_named_extract("Добавь Lionheart и The Fallout")
    titles = {i.title.lower() for i in items}
    assert "lionheart" in titles
    assert "the fallout" in titles


def test_reject_url_as_title():
    assert not _is_valid_title(
        "Текст пользователя: https://www.instagram.com/user/reel/AbCd/"
    )
    assert not _is_valid_title("https://www.tiktok.com/@x/video/1")
    assert _is_valid_title("Lionheart")
