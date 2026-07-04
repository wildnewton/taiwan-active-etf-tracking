"""Nightly pipeline runner for Taiwan Active ETF tracking.

Runs the full workflow:
  1. ETF universe discovery/reconciliation
  2. Browser-enabled scrape
  3. Holding change detection
  4. Manager signal generation
  5. Daily signal report → timestamped file
  6. Traction analysis (active_add/reduce flow) → timestamped file

Usage:
    python3 scripts/nightly_pipeline.py
    python3 scripts/nightly_pipeline.py --db data/active_etf_holdings.sqlite
    python3 scripts/nightly_pipeline.py --report-dir reports
"""
import argparse
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import db
from changes import detect_holding_changes
from discover_active_etfs import discover_and_reconcile
from pipeline import run_daily_scrape_with_browser
from report import generate_signal_report
from signals import generate_manager_signals
from traction_analysis import generate_traction_report


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
    parser.add_argument(
        "--skip-discovery",
        action="store_true",
        help="Skip ETF universe discovery and use the existing DB universe",
    )
    parser.add_argument(
        "--strict-discovery",
        action="store_true",
        help="Fail the nightly run if ETF universe discovery fails",
    )
    args = parser.parse_args()

    db.init_db(args.db)

    if not args.skip_discovery:
        print("Step 1/5: Discovering active ETF universe...")
        try:
            discovery_summary = discover_and_reconcile(args.db)
            print(f"  Discovery summary: {discovery_summary}")
            if not discovery_summary.get("discovery_complete", True):
                failed_markets = discovery_summary.get("failed_markets", [])
                failed_text = ", ".join(
                    f"{item.get('market', 'unknown')}:{item.get('reason', 'unknown')}"
                    for item in failed_markets
                ) or "unknown source"
                message = f"ETF universe discovery incomplete: {failed_text}"
                print(f"⚠️ {message}")
                print("  Continuing with existing DB-backed ETF universe")
                if args.strict_discovery:
                    raise RuntimeError(message)
        except Exception as exc:
            print(f"⚠️ ETF universe discovery failed: {exc}")
            print("  Continuing with existing DB-backed ETF universe")
            if args.strict_discovery:
                raise
    else:
        print("Step 1/5: Skipping ETF universe discovery")

    print("Step 2/5: Running browser-enabled scrape...")
    scrape_summary = run_daily_scrape_with_browser(args.db)
    print(f"  Scrape summary: {scrape_summary}")

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

    moneydj_warnings = scrape_summary.get("moneydj_warnings", [])
    if moneydj_warnings:
        print(f"\n⚠️ MoneyDJ 驗證失敗 ({len(moneydj_warnings)} ETFs):")
        for w in moneydj_warnings:
            print(f"  - {w['etf_code']} ({w['issuer']}): {w['reason']}")
            print(f"    Rows: {w['rows']}, Weight: {w['weight']:.2f}%")
            print(f"    URL: {w['url']}")

    from datetime import date as date_cls
    today_str = date_cls.today().isoformat()
    data_date = scrape_summary.get("data_date")
    if data_date and data_date != today_str:
        print(f"\n⚠️ 資料日期 ≠ 今天：資料日期 {data_date}，今天 {today_str}")
        print(f"  所有持倉和 scrape run 都使用資料日期 {data_date}")

    print("Step 3/5: Detecting holding changes...")
    change_summary = detect_holding_changes()
    print(f"  Change summary: {change_summary}")

    skipped_etfs = change_summary.get("skipped_etfs", [])
    if skipped_etfs:
        print(f"⚠️ 變更偵測跳過以下 ETF: {', '.join(skipped_etfs)}")

    print("Step 4/5: Generating manager signals...")
    signal_summary = generate_manager_signals()
    print(f"  Signal summary: {signal_summary}")

    print("Step 5/6: Generating signal report...")
    report_text = generate_signal_report()

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = report_dir / f"taiwan_active_etf_signal_report_{stamp}.txt"
    report_path.write_text(report_text, encoding="utf-8")

    print("Step 6/6: Generating traction analysis (raw data)...")
    traction_path = None
    try:
        traction_raw = generate_traction_report(
            db_path=args.db,
            window_days=10,
        )
        traction_path = report_dir / f"traction_raw_{stamp}.txt"
        traction_path.write_text(traction_raw, encoding="utf-8")
        print(f"Traction raw data written to: {traction_path}")
    except Exception as exc:
        print(f"⚠️ Traction analysis failed (non-fatal): {exc}")

    print("Nightly Taiwan active ETF pipeline complete")
    print(f"Signal report: {report_path}")
    if traction_path:
        print(f"Traction data: {traction_path}")


if __name__ == "__main__":
    main()
