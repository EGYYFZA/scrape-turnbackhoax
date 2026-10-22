# TurnBackHoax Dataset Collector

A Python command-line project for collecting fact-checking articles and repairing historical article URLs.

## Stack

HTTPX, Selectolax, and python-dateutil.

## Setup

```sh
python -m venv .venv
```

Activate the environment, then install dependencies:

```sh
python -m pip install -r requirements.txt
python scrape_turnbackhoax.py --help
python repair_turnbackhoax_urls.py --help
```

A small collection run:

```sh
python scrape_turnbackhoax.py --mode auto --max 10 --concurrency 1 --out sample.csv
```

Review the source site's access rules and content permissions before collecting or redistributing articles. Existing datasets are research inputs, not a grant of rights to the source articles. Virtual environments and crawl state stay local.

See [SECURITY.md](SECURITY.md).
