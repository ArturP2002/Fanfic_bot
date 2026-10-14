"""Тесты извлечения caption из метаданных скачивания."""

from app.services.media.download import _caption_from_info


def test_caption_from_ytdlp_description():
    assert (
        _caption_from_info({"description": "MOAM\nManacled\nThe Fallout"})
        == "MOAM\nManacled\nThe Fallout"
    )


def test_caption_from_apify_caption():
    assert _caption_from_info({"caption": "Remain Nameless"}) == "Remain Nameless"


def test_caption_prefers_description_over_title():
    info = {"title": "Reel by user", "description": "Measure of a Man"}
    assert _caption_from_info(info) == "Measure of a Man"


def test_caption_empty():
    assert _caption_from_info({}) is None
    assert _caption_from_info({"title": "   "}) is None
