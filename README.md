# scrape-turnbackhoax

Data Mining / Web Scraper for [TurnbackHoax Community](https://turnbackhoax.id) — an Indonesian
fact-checking platform that labels hoaxes, disinformation, and manipulated content.

## Features

- Scrapes article metadata and full content from TurnbackHoax.id
- Uses the **WordPress REST API** (`/wp-json/wp/v2/posts`) when available, and falls back
  to **HTML scraping** automatically
- Extracts: `id`, `title`, `url`, `date`, `author`, `categories`, `tags`, `excerpt`,
  `content`, `verdict` (HOAKS / DISINFORMASI / HOAX / BENAR / MANIPULASI / …)
- Saves results as **JSON** or **CSV**
- Configurable page range, request delay, and timeout
- Polite crawling: configurable delay between requests (default 1 second)

## Requirements

- Python 3.8+
- See `requirements.txt`

## Installation

```bash
pip install -r requirements.txt
```

## Usage

```bash
# Scrape page 1 only, save as JSON (default):
python main.py

# Scrape pages 1–5 and save as JSON:
python main.py --start 1 --end 5

# Scrape pages 1–10 and save as CSV:
python main.py --start 1 --end 10 --format csv --output hasil.csv

# Verbose output:
python main.py --start 1 --end 3 --verbose
```

### All CLI options

| Option | Default | Description |
|---|---|---|
| `--start N` | `1` | First page to scrape |
| `--end N` | *(all pages)* | Last page to scrape (inclusive) |
| `--format` | `json` | Output format: `json` or `csv` |
| `--output FILE` | `turnbackhoax_articles.{json\|csv}` | Output file path |
| `--delay SECONDS` | `1.0` | Delay between HTTP requests |
| `--timeout SECONDS` | `15` | HTTP request timeout |
| `--verbose` | off | Enable DEBUG logging |

## Programmatic usage

```python
from scraper import TurnbackHoaxScraper

scraper = TurnbackHoaxScraper(request_delay=1.0)

# Iterate article by article:
for article in scraper.scrape_pages(start_page=1, end_page=3):
    print(article.title, article.verdict)

# Or dump directly to a file:
scraper.scrape_to_json("output.json", start_page=1, end_page=5)
scraper.scrape_to_csv("output.csv", start_page=1, end_page=5)
```

## Project structure

```
scrape-turnbackhoax/
├── scraper.py          # Core scraping logic (Article dataclass + TurnbackHoaxScraper)
├── main.py             # CLI entry point
├── requirements.txt    # Python dependencies
└── tests/
    └── test_scraper.py # Unit tests (all network calls mocked)
```

## Running tests

```bash
pip install pytest
python -m pytest tests/ -v
```

## Data fields

| Field | Type | Description |
|---|---|---|
| `id` | int | WordPress post ID |
| `title` | str | Article title |
| `url` | str | Permalink |
| `date` | str | Publication date (ISO 8601) |
| `author` | str | Author name |
| `categories` | list[str] | WordPress categories |
| `tags` | list[str] | WordPress tags |
| `excerpt` | str | Short summary |
| `content` | str | Full article text |
| `verdict` | str | Detected label (HOAKS, DISINFORMASI, …) |
