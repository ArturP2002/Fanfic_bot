"""Проверки валидации названий и эвристики извлечения."""

from app.services.extract import (
    _is_valid_title,
    _simple_named_extract,
    extract_from_list_heuristic,
    is_junk_title,
    is_short_catalog_query,
)


def test_valid_short_titles():
    assert _is_valid_title("Lionheart")
    assert _is_valid_title("Manacled")
    assert _is_valid_title("The Auction")
    assert _is_valid_title("MOAM")


def test_short_catalog_query():
    assert is_short_catalog_query("MOAM")
    assert is_short_catalog_query("Measure of a Man")
    assert is_short_catalog_query("DMATMOOBIL")
    assert not is_short_catalog_query("https://ficbook.net/readfic/1")
    assert not is_short_catalog_query("Lionheart\nManacled")
    assert not is_short_catalog_query("потому что я должна была")


def test_simple_extract_keeps_abbreviation():
    result = _simple_named_extract("MOAM")
    assert len(result) == 1
    assert result[0].title == "MOAM"


def test_junk_speech_fragments():
    assert is_junk_title("потому что я должна была")
    assert is_junk_title("номер 10 будет следующим")
    assert not _is_valid_title("я должна сказать вам")
    assert is_junk_title("а" * 81)
    assert is_junk_title("один два три четыре пять шесть семь восемь девять")
    assert is_junk_title("и мне нравится эта идея")
    assert is_junk_title("когда я делаю мою топ-10")
    assert is_junk_title("который я недавно читала")


def test_heuristic_list_ok():
    text = "Lionheart\nManacled\nFallout"
    result = extract_from_list_heuristic(text)
    assert result is not None
    assert len(result) == 3


def test_heuristic_rejects_speech_lines():
    text = "потому что я должна\nя хочу сказать\nсмотрите дальше"
    result = extract_from_list_heuristic(text)
    assert result is None
