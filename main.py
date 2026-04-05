"""
main.py
~~~~~~~
Command-line interface for the TurnbackHoax scraper.

Usage examples
--------------
# Scrape page 1 and save as JSON (default):
    python main.py

# Scrape pages 1-5 and save as CSV:
    python main.py --start 1 --end 5 --format csv --output hasil.csv

# Verbose output:
    python main.py --start 1 --end 3 --verbose
"""

import argparse
import logging
import sys

from scraper import TurnbackHoaxScraper


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scrape-turnbackhoax",
        description="Scrape articles from TurnbackHoax Community (https://turnbackhoax.id)",
    )
    parser.add_argument(
        "--start",
        type=int,
        default=1,
        metavar="N",
        help="First page to scrape (default: 1)",
    )
    parser.add_argument(
        "--end",
        type=int,
        default=None,
        metavar="N",
        help="Last page to scrape, inclusive (default: scrape all pages)",
    )
    parser.add_argument(
        "--format",
        choices=["json", "csv"],
        default="json",
        help="Output format (default: json)",
    )
    parser.add_argument(
        "--output",
        default=None,
        metavar="FILE",
        help=(
            "Output file path (default: turnbackhoax_articles.json or "
            "turnbackhoax_articles.csv depending on --format)"
        ),
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        metavar="SECONDS",
        help="Delay between requests in seconds (default: 1.0)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=15,
        metavar="SECONDS",
        help="HTTP request timeout in seconds (default: 15)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose (DEBUG) logging",
    )
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    output_path = args.output or f"turnbackhoax_articles.{args.format}"

    scraper = TurnbackHoaxScraper(
        request_delay=args.delay,
        timeout=args.timeout,
    )

    if args.format == "json":
        count = scraper.scrape_to_json(output_path, args.start, args.end)
    else:
        count = scraper.scrape_to_csv(output_path, args.start, args.end)

    print(f"Done. {count} articles saved to {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
