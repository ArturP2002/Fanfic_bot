"""Проверки валидации названий и эвристики извлечения."""

from app.services.extract import (
    ExtractedWork,
    _is_valid_title,
    _simple_named_extract,
    extract_from_list_heuristic,
    is_junk_title,
    is_short_catalog_query,
    is_social_handle,
    scrub_authors_that_are_titles,
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


def test_junk_listicle_titles():
    assert is_junk_title("лучшие фф, где Драко одержим Гермионой")
    assert not _is_valid_title("лучшие фф, где Драко одержим Гермионой")
    assert is_junk_title("подборка топ 10 фанфиков")
    assert is_junk_title("фф где Гермиона уезжает")
    assert not is_junk_title("Моя маленькая пташка")
    assert _is_valid_title("Приютные ошибки")


def test_social_handle_author():
    assert not is_social_handle("Mary_Cherry")  # ник на Ficbook, не IG
    assert is_social_handle("@some.user")
    assert not is_social_handle("user.name")
    assert not is_social_handle("SenLinYu")
    assert not is_social_handle("Мария Черри")
    assert not is_social_handle("IsForGood")


def test_scrub_author_that_is_another_title():
    works = scrub_authors_that_are_titles(
        [
            ExtractedWork(title="Принцесса Иерусалимская", confidence=0.9),
            ExtractedWork(
                title="О рыбаках и рыбках",
                author="Принцесса Иерусалимская",
                confidence=0.9,
            ),
        ]
    )
    assert works[1].author is None


def test_ficbook_search_url_uses_search_path():
    from app.services.platform_search import ficbook_search_url

    url = ficbook_search_url("Рождество Темис")
    assert "/search_fanfics?query=" in url
    assert "/search/fanfiction" not in url
    assert "/find?" not in url
