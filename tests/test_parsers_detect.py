"""Тесты определения площадок."""

from app.services.media.download import detect_video_platform
from app.services.parsers import detect_platform


def test_detect_fic_platforms():
    assert detect_platform("https://archiveofourown.org/works/123") == "ao3"
    assert detect_platform("https://ficbook.net/readfic/123456") == "ficbook"
    assert detect_platform("https://www.fanfiction.net/s/123/1/") == "ffn"
    assert detect_platform("https://example.com") is None


def test_detect_video_platforms():
    assert detect_video_platform("https://www.instagram.com/reel/AbCdEf/") == "instagram"
    assert (
        detect_video_platform(
            "https://www.instagram.com/thelibralibraryy/reel/C9h4MCaANml/"
        )
        == "instagram"
    )
    assert detect_video_platform("https://www.tiktok.com/@user/video/123") == "tiktok"
