"""Nightly pipeline runner for Taiwan Active ETF tracking.

Runs the full workflow:
  1. Browser-enabled scrape
  2. Holding change detection
  3. Manager signal generation
  4. Daily signal report → timestamped file

Usage:
    python3 scripts/nightly_pipeline.py
    python3 scripts/nightly_pipeline.py --db data/active_etf_holdings.sqlite
    python3 scripts/nightly_pipeline.py --report-dir reports
"""
import argparse
from datetime import datetime
from pathlib import Path

import db
from pipeline import run_daily_scrape_with_browser
from changes import detect_holding_changes
from signals import generate_manager_signals
from report import generate_signal_report


def main():
    parser = argparse.ArgumentParser(description="Taiwan Active ETF nightly pipeline")
    parser.add_argument(
        "--db",
        default="data/active_etf_holdings.sqlite",
        help="SQLite database path (default: data/active_etf_holdings.sqlite)",
    )
    parser.add_argument(
        "--report-dir",
        default="reports",
        help="Directory for signal report files (default: reports)",
    )
    args = parser.parse_args()

    db.init_db(args.db)

    print("Step 1/4: Running browser-enabled scrape...")
    scrape_summary = run_daily_scrape_with_browser(args.db)
    print(f"  Scrape summary: {scrape_summary}")

    # Check data completeness
    total_etfs = scrape_summary.get("total_etfs")
    moneydj_success = scrape_summary.get("moneydj_success", 0)
    official_success = scrape_summary.get("official_success", 0)
    successful_etfs = moneydj_success + official_success
    if total_etfs is not None and successful_etfs < total_etfs:
        failures = scrape_summary.get("failures", [])
        failed_codes = [
            f.get("etf_code")
            for f in failures
            if isinstance(f, dict) and f.get("etf_code")
        ]
        failure_text = f"（失敗: {', '.join(failed_codes)}）" if failed_codes else ""
        print(
            f"⚠️ 資料不完整: 預期 {total_etfs} 檔 ETF，"
            f"實際取得 {successful_etfs} 檔{failure_text}"
        )

    # Print MoneyDJ validation warnings
    moneydj_warnings = scrape_summary.get("moneydj_warnings", [])
    if moneydj_warnings:
        print(f"\n⚠️ MoneyDJ 驗證失敗 ({len(moneydj_warnings)} ETFs):")
        for w in moneydj_warnings:
            print(f"  - {w['etf_code']} ({w['issuer']}): {w['reason']}")
            print(f"    Rows: {w['rows']}, Weight: {w['weight']:.2f}%")
            print(f"    URL: {w['url']}")

    # Warn if data date differs from today
    from datetime import date as date_cls
    today_str = date_cls.today().isoformat()
    data_date = scrape_summary.get("data_date")
    if data_date and data_date != today_str:
        print(f"\n⚠️ 資料日期 ≠ 今天：資料日期 {data_date}，今天 {today_str}")
        print(f"  所有持倉和 scrape run 都使用資料日期 {data_date}")

    print("Step 2/4: Detecting holding changes...")
    change_summary = detect_holding_changes()
    print(f"  Change summary: {change_summary}")

    # Warn about skipped ETFs
    skipped_etfs = change_summary.get("skipped_etfs", [])
    if skipped_etfs:
        print(f"⚠️ 變更偵測跳過以下 ETF: {', '.join(skipped_etfs)}")

    print("Step 3/4: Generating manager signals...")
    signal_summary = generate_manager_signals()
    print(f"  Signal summary: {signal_summary}")

    print("Step 4/4: Generating signal report...")
    report_text = generate_signal_report()

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = report_dir / f"taiwan_active_etf_signal_report_{stamp}.txt"
    report_path.write_text(report_text, encoding="utf-8")

    print("Nightly Taiwan active ETF pipeline complete")
    print(f"Report written to: {report_path}")


if __name__ == "__main__":
    main()
