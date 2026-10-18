"""Unit-тесты парсинга выдачи AO3/Ficbook и скоринга (без сети)."""

from app.services.platform_search import (
    SearchHit,
    ao3_search_url,
    parse_ao3_search_html,
    parse_ficbook_search_html,
    platform_order,
    rank_hits,
    score_hit,
)

AO3_SEARCH_HTML = """
<html><body>
<ol class="work index group">
  <li class="work blurb group" id="work_1">
    <h4 class="heading">
      <a href="/works/12345">Manacled</a>
      by
      <a rel="author" href="/users/SenLinYu">SenLinYu</a>
    </h4>
  </li>
  <li class="work blurb group" id="work_2">
    <h4 class="heading">
      <a href="/works/99999">Manacled AU</a>
      by
      <a rel="author" href="/users/Other">OtherAuthor</a>
    </h4>
  </li>
</ol>
</body></html>
"""

FICBOOK_SEARCH_HTML = """
<html><body>
<div class="fanfic-inline">
  <h3 class="fanfic-inline-title">
    <a class="visit-link" href="/readfic/123456">Моя маленькая пташка</a>
  </h3>
  <a href="/authors/42">IsForGood</a>
</div>
<article>
  <h3 class="fanfic-inline-title">
    <a class="visit-link" href="/readfic/abc-def">Приютные ошибки</a>
  </h3>
  <a class="author" href="/authors/7">AuthorRu</a>
</article>
<a href="/readfic/123456">Читать</a>
<a class="visit-link" href="/readfic/999?source=premium&premiumVisit=1">Реклама</a>
</body></html>
"""

FICBOOK_EMPTY_HTML = """
<html><body>
<p>По вашему запросу ничего не найдено</p>
<a class="visit-link" href="/readfic/111?source=premium&premiumVisit=1">Стая.</a>
</body></html>
"""


def test_platform_order_cyrillic_first_ficbook():
    assert platform_order("Моя маленькая пташка")[0] == "ficbook"
    assert platform_order("Manacled")[0] == "ao3"


def test_parse_ao3_search_html():
    hits = parse_ao3_search_html(AO3_SEARCH_HTML)
    assert len(hits) == 2
    assert hits[0].title == "Manacled"
    assert hits[0].author == "SenLinYu"
    assert hits[0].url == "https://archiveofourown.org/works/12345"
    assert hits[0].platform == "ao3"


def test_parse_ficbook_search_html():
    hits = parse_ficbook_search_html(FICBOOK_SEARCH_HTML)
    titles = {h.title for h in hits}
    assert "Моя маленькая пташка" in titles
    assert "Приютные ошибки" in titles
    assert "Читать" not in titles
    assert "Реклама" not in titles
    bird = next(h for h in hits if "пташка" in h.title)
    assert bird.url.startswith("https://ficbook.net/readfic/")


def test_parse_ficbook_empty_ignores_premium():
    from app.services.platform_search import parse_ficbook_search_html as parse

    assert parse(FICBOOK_EMPTY_HTML) == []


def test_score_slash_title_alias():
    hit = SearchHit(
        title="Скованные/Manacled",
        author="X",
        url="https://ficbook.net/readfic/1",
        platform="ficbook",
    )
    assert score_hit("Manacled", None, hit) >= 92


def test_score_short_query_not_auto_high_on_long_title():
    long_hit = SearchHit(
        title="Омут потерянной вечности",
        author="A",
        url="https://ficbook.net/readfic/1",
        platform="ficbook",
    )
    loose = SearchHit(
        title="Сhain manacled hands",
        author="B",
        url="https://ficbook.net/readfic/2",
        platform="ficbook",
    )
    assert score_hit("Омут", None, long_hit) < 92
    assert score_hit("Manacled", None, loose) < 92


def test_score_and_rank_high_match():
    hits = [
        SearchHit(
            title="Manacled",
            author="SenLinYu",
            url="https://archiveofourown.org/works/1",
            platform="ao3",
        ),
        SearchHit(
            title="Completely Different",
            author="X",
            url="https://archiveofourown.org/works/2",
            platform="ao3",
        ),
    ]
    ranked = rank_hits("Manacled", "SenLinYu", hits)
    assert ranked
    assert ranked[0].title == "Manacled"
    assert ranked[0].score >= 92
    assert score_hit("Manacled", None, hits[0]) >= 92


def test_ao3_search_url_encodes_title():
    url = ao3_search_url("Measure of a Man", "Nonny")
    assert "archiveofourown.org/works?" in url
    assert "Measure" in url or "Measure+of" in url or "Measure%20" in url
