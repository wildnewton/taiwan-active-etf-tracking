"""Entry point for Taiwan Active ETF Daily Holdings Scraper.

Usage:
    python3 main.py                  # Run daily static scrape
    python3 main.py --with-browser   # Run browser-enabled production scrape
    python3 main.py --report-only    # Generate report from last run
    python3 main.py --db path.db     # Custom DB path
"""
import argparse
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline import run_daily_scrape, run_daily_scrape_with_browser
from report import generate_daily_report


def main():
    parser = argparse.ArgumentParser(description="Taiwan Active ETF Daily Holdings Scraper")
    parser.add_argument(
        "--db",
        default="data/active_etf_holdings.sqlite",
        help="SQLite database path",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Only show report from last run, don't scrape",
    )
    parser.add_argument(
        "--with-browser",
        action="store_true",
        help="Use browser-enabled scrape pipeline with MoneyDJ/official browser fallbacks",
    )
    args = parser.parse_args()

    if args.report_only:
        # TODO: read last run from DB and generate report
        print("Report-only mode not yet implemented")
        return

    if args.with_browser:
        print("Starting browser-enabled daily scrape...")
        summary = run_daily_scrape_with_browser(db_path=args.db)
    else:
        print("Starting daily scrape...")
        summary = run_daily_scrape(db_path=args.db)

    report = generate_daily_report(summary)
    print(report)
    return report


if __name__ == "__main__":
    main()
