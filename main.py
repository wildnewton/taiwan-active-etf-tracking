"""Entry point for Taiwan Active ETF Daily Holdings Scraper.

Usage:
    python3 main.py                  # Run daily scrape
    python3 main.py --report-only    # Generate report from last run
    python3 main.py --db path.db     # Custom DB path
"""
import argparse
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline import run_daily_scrape
from report import generate_daily_report


def main():
    parser = argparse.ArgumentParser(description="Taiwan Active ETF Daily Holdings Scraper")
    parser.add_argument("--db", default="data/active_etf_holdings.sqlite",
                        help="SQLite database path")
    parser.add_argument("--report-only", action="store_true",
                        help="Only show report from last run, don't scrape")
    args = parser.parse_args()

    if args.report_only:
        # TODO: read last run from DB and generate report
        print("Report-only mode not yet implemented")
        return

    print("Starting daily scrape...")
    summary = run_daily_scrape(db_path=args.db)
    report = generate_daily_report(summary)
    print(report)
    return report


if __name__ == "__main__":
    main()
