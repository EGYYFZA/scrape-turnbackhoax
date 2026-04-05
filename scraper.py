"""
scraper.py
~~~~~~~~~~
Core scraping logic for TurnbackHoax Community (https://turnbackhoax.id).

The site runs on WordPress, so this module tries the WordPress REST API first
(``/wp-json/wp/v2/posts``) and falls back to plain HTML scraping when the API
is unavailable or returns an error.

Exported symbols
----------------
- ``Article``  – dataclass holding one scraped article
- ``TurnbackHoaxScraper`` – main scraper class
"""

from __future__ import annotations

import csv
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Generator, List, Optional
from urllib.parse import urljoin, urlparse

import re

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://turnbackhoax.id"
WP_API_BASE = f"{BASE_URL}/wp-json/wp/v2"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; scrape-turnbackhoax/1.0; "
        "+https://github.com/EGYYFZA/scrape-turnbackhoax)"
    ),
    "Accept-Language": "id,en;q=0.9",
}

# Seconds to wait between requests so we don't hammer the server.
REQUEST_DELAY: float = 1.0


@dataclass
class Article:
    """One article scraped from TurnbackHoax."""

    id: int
    title: str
    url: str
    date: str
    author: str
    categories: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    excerpt: str = ""
    content: str = ""
    verdict: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class TurnbackHoaxScraper:
    """
    Scraper for https://turnbackhoax.id.

    Parameters
    ----------
    request_delay:
        Seconds to sleep between HTTP requests (default: 1.0).
    timeout:
        HTTP request timeout in seconds (default: 15).
    """

    def __init__(
        self,
        request_delay: float = REQUEST_DELAY,
        timeout: int = 15,
    ) -> None:
        self._delay = request_delay
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(DEFAULT_HEADERS)
        self._api_available: Optional[bool] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scrape_pages(
        self,
        start_page: int = 1,
        end_page: Optional[int] = None,
    ) -> Generator[Article, None, None]:
        """
        Yield :class:`Article` objects page by page.

        Parameters
        ----------
        start_page:
            First page index (1-based).
        end_page:
            Last page index (inclusive).  ``None`` means scrape until there
            are no more pages.
        """
        if self._api_available is None:
            self._api_available = self._check_api()

        if self._api_available:
            logger.info("Using WordPress REST API")
            yield from self._scrape_via_api(start_page, end_page)
        else:
            logger.info("WordPress REST API unavailable – falling back to HTML scraping")
            yield from self._scrape_via_html(start_page, end_page)

    def scrape_to_json(
        self,
        output_path: str,
        start_page: int = 1,
        end_page: Optional[int] = None,
    ) -> int:
        """
        Scrape articles and write them to a JSON file.

        Returns the number of articles saved.
        """
        articles = list(self.scrape_pages(start_page, end_page))
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump([a.to_dict() for a in articles], fh, ensure_ascii=False, indent=2)
        logger.info("Saved %d articles to %s", len(articles), output_path)
        return len(articles)

    def scrape_to_csv(
        self,
        output_path: str,
        start_page: int = 1,
        end_page: Optional[int] = None,
    ) -> int:
        """
        Scrape articles and write them to a CSV file.

        Returns the number of articles saved.
        """
        articles = list(self.scrape_pages(start_page, end_page))
        if not articles:
            logger.warning("No articles found.")
            return 0

        fieldnames = list(Article.__dataclass_fields__.keys())
        with open(output_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for a in articles:
                row = a.to_dict()
                # Flatten list fields to semicolon-separated strings for CSV.
                row["categories"] = "; ".join(row["categories"])
                row["tags"] = "; ".join(row["tags"])
                writer.writerow(row)

        logger.info("Saved %d articles to %s", len(articles), output_path)
        return len(articles)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get(self, url: str, **kwargs: Any) -> Optional[requests.Response]:
        """GET *url* and return the response, or ``None`` on error."""
        try:
            resp = self._session.get(url, timeout=self._timeout, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            logger.warning("Request failed for %s: %s", url, exc)
            return None

    def _sleep(self) -> None:
        if self._delay > 0:
            time.sleep(self._delay)

    # ------------------------------------------------------------------
    # WordPress REST API path
    # ------------------------------------------------------------------

    def _check_api(self) -> bool:
        """Return True if the WP REST API is reachable."""
        resp = self._get(f"{WP_API_BASE}/posts", params={"per_page": 1, "page": 1})
        return resp is not None and resp.status_code == 200

    def _scrape_via_api(
        self,
        start_page: int,
        end_page: Optional[int],
    ) -> Generator[Article, None, None]:
        """Yield articles by consuming the WordPress REST API."""
        page = start_page
        while True:
            if end_page is not None and page > end_page:
                break

            url = f"{WP_API_BASE}/posts"
            params: Dict[str, Any] = {
                "per_page": 20,
                "page": page,
                "_embed": 1,
            }
            resp = self._get(url, params=params)
            if resp is None:
                break

            posts = resp.json()
            if not posts:
                break

            for post in posts:
                article = self._parse_api_post(post)
                if article:
                    yield article

            # Check if there are more pages.
            total_pages = int(resp.headers.get("X-WP-TotalPages", 1))
            if page >= total_pages:
                break

            page += 1
            self._sleep()

    def _parse_api_post(self, post: Dict[str, Any]) -> Optional[Article]:
        """Convert a raw WP REST API post dict into an :class:`Article`."""
        try:
            embedded = post.get("_embedded", {})

            # Author
            authors = embedded.get("author", [{}])
            author = authors[0].get("name", "") if authors else ""

            # Categories & tags
            terms = embedded.get("wp:term", [])
            categories: List[str] = []
            tags: List[str] = []
            for term_group in terms:
                for term in term_group:
                    taxonomy = term.get("taxonomy", "")
                    name = term.get("name", "")
                    if taxonomy == "category":
                        categories.append(name)
                    elif taxonomy == "post_tag":
                        tags.append(name)

            # Strip HTML from excerpt/content using BS4.
            raw_excerpt = post.get("excerpt", {}).get("rendered", "")
            excerpt = BeautifulSoup(raw_excerpt, "lxml").get_text(strip=True)

            raw_content = post.get("content", {}).get("rendered", "")
            content = BeautifulSoup(raw_content, "lxml").get_text(separator="\n", strip=True)

            return Article(
                id=post["id"],
                title=BeautifulSoup(
                    post.get("title", {}).get("rendered", ""), "lxml"
                ).get_text(strip=True),
                url=post.get("link", ""),
                date=post.get("date", ""),
                author=author,
                categories=categories,
                tags=tags,
                excerpt=excerpt,
                content=content,
                verdict=self._extract_verdict(content),
            )
        except (KeyError, TypeError) as exc:
            logger.warning("Failed to parse API post: %s", exc)
            return None

    # ------------------------------------------------------------------
    # HTML scraping path
    # ------------------------------------------------------------------

    def _scrape_via_html(
        self,
        start_page: int,
        end_page: Optional[int],
    ) -> Generator[Article, None, None]:
        """Yield articles by scraping the HTML listing pages."""
        page = start_page
        while True:
            if end_page is not None and page > end_page:
                break

            if page == 1:
                listing_url = BASE_URL
            else:
                listing_url = f"{BASE_URL}/page/{page}/"

            logger.info("Scraping listing page %d: %s", page, listing_url)
            resp = self._get(listing_url)
            if resp is None:
                break

            soup = BeautifulSoup(resp.text, "lxml")
            article_links = self._extract_listing_links(soup)

            if not article_links:
                logger.info("No articles found on page %d – stopping.", page)
                break

            for link in article_links:
                article = self._scrape_article_html(link)
                if article:
                    yield article
                self._sleep()

            # Check for a "next page" link.
            if not self._has_next_page(soup):
                break

            page += 1
            self._sleep()

    def _extract_listing_links(self, soup: BeautifulSoup) -> List[str]:
        """Return article URLs from a listing page."""
        links: List[str] = []
        # TurnbackHoax uses the standard WordPress "hentry" article structure.
        for article_tag in soup.find_all("article"):
            a_tag = article_tag.find("a", href=True)
            if a_tag and a_tag["href"].startswith(BASE_URL):
                links.append(a_tag["href"])
        # Deduplicate while preserving order.
        seen: set = set()
        deduped: List[str] = []
        for link in links:
            if link not in seen:
                seen.add(link)
                deduped.append(link)
        return deduped

    def _has_next_page(self, soup: BeautifulSoup) -> bool:
        """Return True if there is a visible next-page link."""
        next_link = soup.find("a", class_="next") or soup.find(
            "a", string=lambda t: t and "next" in t.lower()
        )
        return next_link is not None

    def _scrape_article_html(self, url: str) -> Optional[Article]:
        """Scrape a single article page and return an :class:`Article`."""
        resp = self._get(url)
        if resp is None:
            return None

        soup = BeautifulSoup(resp.text, "lxml")

        # --- title ---
        title_tag = soup.find("h1", class_="entry-title") or soup.find("h1")
        title = title_tag.get_text(strip=True) if title_tag else ""

        # --- date ---
        time_tag = soup.find("time", class_="entry-date") or soup.find("time")
        date = time_tag.get("datetime", time_tag.get_text(strip=True)) if time_tag else ""

        # --- author ---
        author_tag = (
            soup.find(class_="author vcard")
            or soup.find(rel="author")
            or soup.find(class_="author")
        )
        author = author_tag.get_text(strip=True) if author_tag else ""

        # --- categories ---
        categories: List[str] = [
            a.get_text(strip=True)
            for a in soup.find_all("a", rel="category tag")
        ]

        # --- tags ---
        tags: List[str] = []
        tags_section = soup.find(class_="tags-links") or soup.find(class_="entry-tags")
        if tags_section:
            tags = [a.get_text(strip=True) for a in tags_section.find_all("a")]

        # --- content ---
        content_div = (
            soup.find("div", class_="entry-content")
            or soup.find("div", class_="post-content")
            or soup.find("article")
        )
        content = (
            content_div.get_text(separator="\n", strip=True) if content_div else ""
        )

        # --- excerpt (first paragraph) ---
        excerpt = ""
        if content_div:
            first_p = content_div.find("p")
            if first_p:
                excerpt = first_p.get_text(strip=True)

        # --- derive a numeric id from the URL (WP uses ?p=N or post slug) ---
        post_id = self._id_from_url(url, soup)

        return Article(
            id=post_id,
            title=title,
            url=url,
            date=date,
            author=author,
            categories=categories,
            tags=tags,
            excerpt=excerpt,
            content=content,
            verdict=self._extract_verdict(content),
        )

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _id_from_url(url: str, soup: BeautifulSoup) -> int:
        """Try to derive a numeric post ID from the URL or page markup."""
        # WP sometimes stores id in a body class like "postid-12345".
        body = soup.find("body")
        if body:
            for cls in body.get("class", []):
                if cls.startswith("postid-"):
                    try:
                        return int(cls.split("-", 1)[1])
                    except (ValueError, IndexError):
                        pass

        # Try ?p=N query param.
        parsed = urlparse(url)
        for part in parsed.query.split("&"):
            if part.startswith("p="):
                try:
                    return int(part[2:])
                except ValueError:
                    pass

        # Fall back to 0.
        return 0

    @staticmethod
    def _extract_verdict(content: str) -> str:
        """
        Try to extract a verdict label (e.g. HOAKS, DISINFORMASI) from
        the article content.  TurnbackHoax commonly includes these labels
        in all-caps near the start of the content.
        """
        verdicts = [
            "HOAKS",
            "HOAX",
            "DISINFORMASI",
            "MANIPULASI",
            "MISLEADING",
            "FAKTA",
            "BENAR",
            "SALAH",
        ]
        upper = content.upper()
        for verdict in verdicts:
            if verdict in upper:
                return verdict
        return ""
