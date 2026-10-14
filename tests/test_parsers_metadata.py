"""Тесты извлечения метаданных со страниц площадок."""

import pytest

from app.services.parsers import ao3, ficbook
from app.services.parsers.base import WorkPageUnavailableError, canonical_url, detect_platform
from app.services.works import split_translation_urls

FICBOOK_AUTHOR_HTML = """
<h1>Помоги мне отомстить</h1>
<div class="avatar-cropper"><a href="/authors/1424194"><img></a></div>
<span class="d-inline-flex">
  <a class="word-break" href="/authors/1424194" itemprop="author">Verity in wine</a>
</span>
<span>автор</span>
<div class="avatar-cropper"><a href="/authors/489561"><img></a></div>
<a class="word-break" href="/authors/489561">Nina16</a><span>бета</span>
"""

FICBOOK_TRANSLATION_HTML = """
<h1>Зеленый свет/Green Light</h1>
<div class="avatar-cropper"><a href="/authors/4016335"><img></a></div>
<a class="word-break" href="/authors/4016335" itemprop="author">MissMercy97</a>
<span>переводчик</span>
<div class="mb-10">
  <strong>Автор оригинала:</strong>
  <div><a href="/translations/by_author?author=SereneMusafir">SereneMusafir</a></div>
</div>
<div class="mb-10">
  <strong>Оригинал:</strong>
  <div>https://archiveofourown.org/works/32535493/chapters/80699710</div>
</div>
"""


def _ao3_html(status_label: str | None, chapters: str) -> str:
    status = (
        f'<dt class="status">{status_label}</dt><dd class="status">2024-10-12</dd>'
        if status_label
        else ""
    )
    return f"""
    <h2 class="title heading">Nomura</h2>
    <a rel="author">haironmylip</a>
    <dl class="stats">
      <dt class="published">Published:</dt><dd class="published">2022-10-24</dd>
      {status}
      <dt class="chapters">Chapters:</dt><dd class="chapters">{chapters}</dd>
    </dl>
    """


def test_ficbook_author_skips_empty_avatar_link():
    work = ficbook.parse(FICBOOK_AUTHOR_HTML, "https://ficbook.net/readfic/3721281/")
    assert work.author == "Verity in wine"


def test_ficbook_translation_uses_original_author():
    work = ficbook.parse(
        FICBOOK_TRANSLATION_HTML,
        "https://ficbook.net/readfic/0199cf9e-7cb5-7dd1-966b-94b8b23d0a7c/",
    )
    assert work.author == "SereneMusafir"
    assert work.original_url == "https://archiveofourown.org/works/32535493/chapters/80699710"


def test_ficbook_without_translation_has_no_original():
    work = ficbook.parse(FICBOOK_AUTHOR_HTML, "https://ficbook.net/readfic/3721281")
    assert work.original_url is None


def test_canonical_url():
    assert (
        canonical_url("https://archiveofourown.org/works/26523892?view_full_work=true")
        == "https://archiveofourown.org/works/26523892"
    )
    assert (
        canonical_url("http://archiveofourown.org/works/46351063/chapters/116719615#workskin")
        == "https://archiveofourown.org/works/46351063"
    )
    assert canonical_url("https://ficbook.net/readfic/3721281/") == (
        "https://ficbook.net/readfic/3721281"
    )
    assert canonical_url("https://fanfiction.net/s/13304436/1/The-Auction") == (
        "https://www.fanfiction.net/s/13304436"
    )


def test_split_translation_urls():
    ficbook_url = "https://ficbook.net/readfic/9717885"
    assert split_translation_urls(
        ficbook_url, "https://archiveofourown.org/works/23875939/chapters/57393508"
    ) == ("https://archiveofourown.org/works/23875939", ficbook_url)
    assert split_translation_urls(
        ficbook_url, "http://www.dramione.org/viewstory.php?sid=1"
    ) == (ficbook_url, None)
    assert split_translation_urls(ficbook_url, None) == (ficbook_url, None)


def test_ao3_status_from_label():
    url = "https://archiveofourown.org/works/42601638"
    assert ao3.parse(_ao3_html("Completed:", "27/27"), url).status == "completed"
    assert ao3.parse(_ao3_html("Updated:", "5/?"), url).status == "in_progress"


def test_ao3_login_page_is_not_a_work():
    login_page = "<title>New Session | Archive of Our Own</title><form id='new_user'></form>"
    with pytest.raises(WorkPageUnavailableError):
        ao3.parse(login_page, "https://archiveofourown.org/works/23875939")


def test_ao3_status_from_chapters_without_label():
    url = "https://archiveofourown.org/works/1"
    assert ao3.parse(_ao3_html(None, "1/1"), url).status == "completed"


def test_detect_ficbook_uuid_link():
    url = "https://ficbook.net/readfic/0199cf9e-7cb5-7dd1-966b-94b8b23d0a7c/"
    assert detect_platform(url) == "ficbook"
