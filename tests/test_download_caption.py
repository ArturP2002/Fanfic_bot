"""Тесты извлечения caption и input для Apify."""

from app.services.media.download import (
    _apify_run_input,
    _caption_from_info,
    _clean_instagram_url,
)


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


def test_clean_instagram_url_strips_tracking():
    raw = "https://www.instagram.com/reel/DKSEKSPTlC1/?psln=eG9paWswdWNsZ2N6"
    assert _clean_instagram_url(raw) == "https://www.instagram.com/reel/DKSEKSPTlC1/"


def test_apify_instagram_input_puts_url_in_username():
    url = "https://www.instagram.com/reel/DKSEKSPTlC1/?psln=abc"
    payload = _apify_run_input(url, "instagram")
    assert payload == {
        "username": ["https://www.instagram.com/reel/DKSEKSPTlC1/"],
        "resultsLimit": 1,
    }
    assert "directUrls" not in payload
    assert payload["username"]  # не пустой — иначе Apify 400 minItems


def test_apify_tiktok_input():
    url = "https://www.tiktok.com/@user/video/123"
    payload = _apify_run_input(url, "tiktok")
    assert payload["postURLs"] == [url]
    assert payload["shouldDownloadVideos"] is True
