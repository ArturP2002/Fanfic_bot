"""Парсеры страниц фанфиков."""

from app.services.parsers.base import (
    ParsedWork,
    canonical_url,
    detect_platform,
    parse_fic_url,
)

__all__ = ["ParsedWork", "canonical_url", "detect_platform", "parse_fic_url"]
