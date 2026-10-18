"""Тесты карточек досье и batch-списков."""

from types import SimpleNamespace

from app.models.enums import WorkStatus
from app.services.extract import ExtractedWork
from app.services.formatting import (
    format_batch_list_from_payload,
    format_catalog_confirm,
    format_single_work,
    work_has_rich_meta,
)
from app.services.resolution import ResolvedItem


def _work(**kwargs):
    defaults = {
        "title": "Manacled",
        "author": "SenLinYu",
        "pairing": "Hermione Granger/Draco Malfoy",
        "fandom": "Harry Potter",
        "status": WorkStatus.COMPLETED.value,
        "volume_label": "длинный",
        "tags": ["Angst", "Drama"],
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_work_has_rich_meta():
    assert work_has_rich_meta(_work())
    assert not work_has_rich_meta(
        _work(pairing=None, fandom=None, volume_label=None, tags=None, status=WorkStatus.UNKNOWN.value)
    )


def test_format_single_work_includes_pairing():
    item = ResolvedItem(
        extracted=ExtractedWork(title="Manacled", confidence=0.9),
        work=_work(),
    )
    text = format_single_work(item)
    assert "Пейринг:" in text
    assert "Hermione" in text
    assert "Фандом:" in text


def test_format_single_work_without_meta():
    item = ResolvedItem(
        extracted=ExtractedWork(title="Новинка", confidence=0.9),
        work=_work(
            title="Новинка",
            author=None,
            pairing=None,
            fandom=None,
            volume_label=None,
            tags=None,
            status=WorkStatus.UNKNOWN.value,
        ),
    )
    text = format_single_work(item)
    assert "Метаданных пока нет" in text


def test_format_catalog_confirm_with_meta():
    item = ResolvedItem(
        extracted=ExtractedWork(title="Manacled", confidence=0.8),
        work=_work(),
        needs_catalog_confirm=True,
    )
    text = format_catalog_confirm(item)
    assert "Похоже, нашла новый" in text
    assert "Пейринг:" in text


def test_batch_list_from_payload_shows_meta_or_hint():
    payload = {
        "items": [
            {
                "title": "Manacled",
                "author": "SenLinYu",
                "work_id": 1,
                "has_meta": True,
                "pairing": "Dramione",
                "fandom": "HP",
                "volume_label": "длинный",
                "status": "completed",
                "tags": None,
            },
            {
                "title": "Только название",
                "author": None,
                "work_id": 2,
                "has_meta": False,
                "needs_catalog_confirm": True,
            },
        ]
    }
    text = format_batch_list_from_payload(payload)
    assert "Пейринг: Dramione" in text
    assert "нет досье" in text
    # Пустая строка между пунктами
    assert "\n\n2. Только название" in text
