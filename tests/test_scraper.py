"""
tests/test_scraper.py
~~~~~~~~~~~~~~~~~~~~~
Unit tests for the TurnbackHoax scraper.

All network calls are mocked so the tests run offline.
"""

import csv
import json
import os
import sys
import textwrap
from unittest.mock import MagicMock, patch

import pytest

# Ensure the project root is on the path.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scraper import Article, TurnbackHoaxScraper


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

SAMPLE_POST = {
    "id": 42,
    "title": {"rendered": "Ini Adalah Hoaks"},
    "link": "https://turnbackhoax.id/2024/01/01/ini-adalah-hoaks/",
    "date": "2024-01-01T10:00:00",
    "excerpt": {"rendered": "<p>Ringkasan singkat artikel.</p>"},
    "content": {"rendered": "<p>Konten lengkap: HOAKS beredar di media sosial.</p>"},
    "_embedded": {
        "author": [{"name": "Admin TBH"}],
        "wp:term": [
            [{"taxonomy": "category", "name": "Hoaks"}],
            [{"taxonomy": "post_tag", "name": "covid"}, {"taxonomy": "post_tag", "name": "vaksin"}],
        ],
    },
}

SAMPLE_HTML_LISTING = textwrap.dedent("""\
    <html><body class="home blog">
    <article id="post-10" class="hentry">
      <h2 class="entry-title">
        <a href="https://turnbackhoax.id/2024/01/02/artikel-satu/">Artikel Satu</a>
      </h2>
    </article>
    <article id="post-11" class="hentry">
      <h2 class="entry-title">
        <a href="https://turnbackhoax.id/2024/01/01/artikel-dua/">Artikel Dua</a>
      </h2>
    </article>
    </body></html>
""")

SAMPLE_HTML_ARTICLE = textwrap.dedent("""\
    <html>
    <body class="single-post postid-10">
    <article>
      <h1 class="entry-title">Artikel Satu</h1>
      <time class="entry-date" datetime="2024-01-02T08:00:00">2 Januari 2024</time>
      <span class="author vcard">Penulis A</span>
      <a rel="category tag" href="/category/hoaks/">Hoaks</a>
      <div class="entry-content">
        <p>Paragraf pertama berisi DISINFORMASI yang beredar.</p>
        <p>Paragraf kedua.</p>
      </div>
    </article>
    </body>
    </html>
""")


def _make_response(json_data=None, text=None, status_code=200, headers=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {}
    if json_data is not None:
        resp.json.return_value = json_data
    if text is not None:
        resp.text = text
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# Article dataclass tests
# ---------------------------------------------------------------------------

class TestArticle:
    def test_to_dict_contains_all_fields(self):
        a = Article(
            id=1,
            title="Test",
            url="https://example.com",
            date="2024-01-01",
            author="Author",
            categories=["Cat1"],
            tags=["tag1"],
            excerpt="Short",
            content="Long content",
            verdict="HOAKS",
        )
        d = a.to_dict()
        assert d["id"] == 1
        assert d["title"] == "Test"
        assert d["verdict"] == "HOAKS"
        assert d["categories"] == ["Cat1"]

    def test_default_list_fields(self):
        a = Article(id=0, title="", url="", date="", author="")
        assert a.categories == []
        assert a.tags == []


# ---------------------------------------------------------------------------
# TurnbackHoaxScraper._check_api
# ---------------------------------------------------------------------------

class TestCheckApi:
    def test_api_available_returns_true(self):
        scraper = TurnbackHoaxScraper(request_delay=0)
        mock_resp = _make_response(json_data=[], status_code=200)
        with patch.object(scraper, "_get", return_value=mock_resp):
            assert scraper._check_api() is True

    def test_api_unavailable_returns_false(self):
        scraper = TurnbackHoaxScraper(request_delay=0)
        with patch.object(scraper, "_get", return_value=None):
            assert scraper._check_api() is False


# ---------------------------------------------------------------------------
# _parse_api_post
# ---------------------------------------------------------------------------

class TestParseApiPost:
    def test_parses_all_fields(self):
        scraper = TurnbackHoaxScraper(request_delay=0)
        article = scraper._parse_api_post(SAMPLE_POST)

        assert article is not None
        assert article.id == 42
        assert article.title == "Ini Adalah Hoaks"
        assert article.url == "https://turnbackhoax.id/2024/01/01/ini-adalah-hoaks/"
        assert article.date == "2024-01-01T10:00:00"
        assert article.author == "Admin TBH"
        assert "Hoaks" in article.categories
        assert "covid" in article.tags
        assert "vaksin" in article.tags
        assert "Ringkasan" in article.excerpt
        assert "HOAKS" in article.content
        assert article.verdict == "HOAKS"

    def test_returns_none_on_bad_data(self):
        scraper = TurnbackHoaxScraper(request_delay=0)
        assert scraper._parse_api_post({}) is None


# ---------------------------------------------------------------------------
# _extract_verdict
# ---------------------------------------------------------------------------

class TestExtractVerdict:
    @pytest.mark.parametrize("content,expected", [
        ("Beredar HOAKS tentang ...", "HOAKS"),
        ("Ini adalah DISINFORMASI yang ...", "DISINFORMASI"),
        ("Klaim ini BENAR adanya.", "BENAR"),
        ("Tidak ada label apapun.", ""),
        ("HOAX besar-besaran.", "HOAX"),
    ])
    def test_verdicts(self, content, expected):
        assert TurnbackHoaxScraper._extract_verdict(content) == expected


# ---------------------------------------------------------------------------
# HTML scraping helpers
# ---------------------------------------------------------------------------

class TestHtmlHelpers:
    def test_extract_listing_links(self):
        from bs4 import BeautifulSoup
        scraper = TurnbackHoaxScraper(request_delay=0)
        soup = BeautifulSoup(SAMPLE_HTML_LISTING, "lxml")
        links = scraper._extract_listing_links(soup)
        assert len(links) == 2
        assert "artikel-satu" in links[0]
        assert "artikel-dua" in links[1]

    def test_no_duplicate_links(self):
        from bs4 import BeautifulSoup
        # Duplicate the article tags.
        html = SAMPLE_HTML_LISTING.replace(
            "</body>",
            '<article><a href="https://turnbackhoax.id/2024/01/02/artikel-satu/">dup</a></article></body>',
        )
        scraper = TurnbackHoaxScraper(request_delay=0)
        soup = BeautifulSoup(html, "lxml")
        links = scraper._extract_listing_links(soup)
        assert links.count("https://turnbackhoax.id/2024/01/02/artikel-satu/") == 1

    def test_scrape_article_html(self):
        scraper = TurnbackHoaxScraper(request_delay=0)
        mock_resp = _make_response(text=SAMPLE_HTML_ARTICLE)
        with patch.object(scraper, "_get", return_value=mock_resp):
            article = scraper._scrape_article_html(
                "https://turnbackhoax.id/2024/01/02/artikel-satu/"
            )
        assert article is not None
        assert article.title == "Artikel Satu"
        assert article.date == "2024-01-02T08:00:00"
        assert article.author == "Penulis A"
        assert "Hoaks" in article.categories
        assert article.verdict == "DISINFORMASI"
        assert article.id == 10  # extracted from postid-10 body class

    def test_id_from_url_postid_class(self):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup('<body class="single postid-99"></body>', "lxml")
        assert TurnbackHoaxScraper._id_from_url("https://example.com/", soup) == 99

    def test_id_from_url_query_param(self):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup("<body></body>", "lxml")
        assert TurnbackHoaxScraper._id_from_url("https://example.com/?p=55", soup) == 55

    def test_id_from_url_fallback(self):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup("<body></body>", "lxml")
        assert TurnbackHoaxScraper._id_from_url("https://example.com/slug/", soup) == 0


# ---------------------------------------------------------------------------
# API scraping integration (mocked)
# ---------------------------------------------------------------------------

class TestScrapeViaApi:
    def test_yields_articles(self):
        scraper = TurnbackHoaxScraper(request_delay=0)
        scraper._api_available = True

        resp1 = _make_response(
            json_data=[SAMPLE_POST],
            headers={"X-WP-TotalPages": "1"},
        )
        with patch.object(scraper, "_get", return_value=resp1):
            articles = list(scraper.scrape_pages(start_page=1, end_page=1))

        assert len(articles) == 1
        assert articles[0].title == "Ini Adalah Hoaks"

    def test_stops_on_empty_page(self):
        scraper = TurnbackHoaxScraper(request_delay=0)
        scraper._api_available = True

        resp = _make_response(json_data=[], headers={"X-WP-TotalPages": "5"})
        with patch.object(scraper, "_get", return_value=resp):
            articles = list(scraper.scrape_pages(start_page=1, end_page=5))

        assert articles == []


# ---------------------------------------------------------------------------
# HTML scraping integration (mocked)
# ---------------------------------------------------------------------------

class TestScrapeViaHtml:
    def test_yields_articles(self):
        scraper = TurnbackHoaxScraper(request_delay=0)
        scraper._api_available = False

        listing_resp = _make_response(text=SAMPLE_HTML_LISTING)
        article_resp = _make_response(text=SAMPLE_HTML_ARTICLE)

        def mock_get(url, **kwargs):
            if "turnbackhoax.id/2024" in url:
                return article_resp
            return listing_resp

        with patch.object(scraper, "_get", side_effect=mock_get):
            articles = list(scraper.scrape_pages(start_page=1, end_page=1))

        assert len(articles) == 2


# ---------------------------------------------------------------------------
# Output: JSON and CSV
# ---------------------------------------------------------------------------

class TestOutputMethods:
    def test_scrape_to_json(self, tmp_path):
        scraper = TurnbackHoaxScraper(request_delay=0)
        scraper._api_available = True

        resp = _make_response(
            json_data=[SAMPLE_POST],
            headers={"X-WP-TotalPages": "1"},
        )
        out = str(tmp_path / "out.json")
        with patch.object(scraper, "_get", return_value=resp):
            count = scraper.scrape_to_json(out, start_page=1, end_page=1)

        assert count == 1
        with open(out, encoding="utf-8") as fh:
            data = json.load(fh)
        assert data[0]["title"] == "Ini Adalah Hoaks"

    def test_scrape_to_csv(self, tmp_path):
        scraper = TurnbackHoaxScraper(request_delay=0)
        scraper._api_available = True

        resp = _make_response(
            json_data=[SAMPLE_POST],
            headers={"X-WP-TotalPages": "1"},
        )
        out = str(tmp_path / "out.csv")
        with patch.object(scraper, "_get", return_value=resp):
            count = scraper.scrape_to_csv(out, start_page=1, end_page=1)

        assert count == 1
        with open(out, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        assert rows[0]["title"] == "Ini Adalah Hoaks"
        assert rows[0]["verdict"] == "HOAKS"
        # Categories should be semicolon-separated string.
        assert "Hoaks" in rows[0]["categories"]

    def test_scrape_to_csv_empty(self, tmp_path):
        scraper = TurnbackHoaxScraper(request_delay=0)
        scraper._api_available = True

        resp = _make_response(json_data=[], headers={"X-WP-TotalPages": "1"})
        out = str(tmp_path / "empty.csv")
        with patch.object(scraper, "_get", return_value=resp):
            count = scraper.scrape_to_csv(out, start_page=1, end_page=1)

        assert count == 0


# ---------------------------------------------------------------------------
# CLI (main.py)
# ---------------------------------------------------------------------------

class TestCli:
    def test_json_output(self, tmp_path):
        from main import main

        out = str(tmp_path / "cli_out.json")
        mock_scraper = MagicMock()
        mock_scraper.return_value.scrape_to_json.return_value = 3

        with patch("main.TurnbackHoaxScraper", mock_scraper):
            ret = main(["--start", "1", "--end", "2", "--format", "json", "--output", out])

        assert ret == 0
        mock_scraper.return_value.scrape_to_json.assert_called_once_with(out, 1, 2)

    def test_csv_output(self, tmp_path):
        from main import main

        out = str(tmp_path / "cli_out.csv")
        mock_scraper = MagicMock()
        mock_scraper.return_value.scrape_to_csv.return_value = 5

        with patch("main.TurnbackHoaxScraper", mock_scraper):
            ret = main(["--start", "1", "--end", "1", "--format", "csv", "--output", out])

        assert ret == 0
        mock_scraper.return_value.scrape_to_csv.assert_called_once_with(out, 1, 1)
