"""Nightly pipeline runner for Taiwan Active ETF tracking.

Runs the full workflow:
  1. ETF universe discovery/reconciliation
  2. Browser-enabled scrape
  3. Holding change detection
  4. Manager intent rollup generation
  5. Manager signal generation
  6. Daily signal report → date-only primary file + timestamped archive
  7. Traction analysis (active_add/reduce flow) → date-only primary file + timestamped archive

Usage:
    python3 scripts/nightly_pipeline.py
    python3 scripts/nightly_pipeline.py --db data/active_etf_holdings.sqlite
    python3 scripts/nightly_pipeline.py --report-dir reports
    python3 scripts/nightly_pipeline.py --try-run
"""

import argparse
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

import db
from changes import detect_holding_changes, get_latest_valid_date
from discover_active_etfs import discover_and_reconcile
from manager_intent import generate_manager_intent_rollups
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
    parser.add_argument(
        "--try-run",
        action="store_true",
        help=(
            "Run the real nightly workflow against disposable DB/report state, "
            "then discard all changes"
        ),
    )
    args = parser.parse_args()

    runner = run_try_run if args.try_run else run_nightly_pipeline
    runner(
        args.db,
        args.report_dir,
        skip_discovery=args.skip_discovery,
        strict_discovery=args.strict_discovery,
    )


def _backup_sqlite_database(source_path: Path, target_path: Path):
    """Copy a live SQLite database into a consistent disposable snapshot."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    source_uri = f"{source_path.resolve().as_uri()}?mode=ro"
    source = sqlite3.connect(source_uri, uri=True)
    target = sqlite3.connect(target_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def _print_try_run_outputs(result):
    if not isinstance(result, dict):
        return

    if "signal_report" in result:
        print("\n=== TRY-RUN SIGNAL REPORT ===")
        print(result.get("signal_report") or "(empty report)")

    if result.get("traction_report") is not None:
        print("\n=== TRY-RUN TRACTION REPORT ===")
        print(result.get("traction_report") or "(empty report)")


def run_try_run(
    db_path,
    report_dir,
    *,
    skip_discovery=False,
    strict_discovery=False,
):
    """Execute the real nightly pipeline on disposable state and discard it."""
    if db_path == ":memory:":
        raise ValueError("--try-run requires a file-backed SQLite database")

    production_db = Path(db_path)
    production_reports = Path(report_dir)
    previous_db_path = db._DB_PATH

    print("=== NIGHTLY TRY-RUN ===")
    print(f"Production DB (read-only source): {production_db}")
    print(f"Production report directory (untouched): {production_reports}")

    try:
        with tempfile.TemporaryDirectory(prefix="taiwan-active-etf-try-run-") as temp_root:
            temp_root_path = Path(temp_root)
            disposable_db = temp_root_path / "active_etf_holdings.sqlite"
            disposable_reports = temp_root_path / "reports"

            if production_db.exists():
                _backup_sqlite_database(production_db, disposable_db)
                print(f"Disposable DB snapshot: {disposable_db}")
            else:
                print("Production DB does not exist; try-run will start from an empty disposable DB")

            print(f"Disposable report directory: {disposable_reports}")
            result = run_nightly_pipeline(
                str(disposable_db),
                str(disposable_reports),
                skip_discovery=skip_discovery,
                strict_discovery=strict_discovery,
            )
            _print_try_run_outputs(result)
            return result
    finally:
        # The process normally exits after main(), but restoring the module target
        # keeps programmatic/test callers from pointing at the deleted temp DB.
        db._DB_PATH = previous_db_path
        print(
            "TRY-RUN complete: temporary database, SQLite sidecars, and reports "
            "were discarded; production state was not modified."
        )


def _resolve_target_data_date(scrape_summary, db_path):
    """Validate the single holdings date produced and persisted by this run."""
    target_data_date = scrape_summary.get("expected_data_date")
    if not target_data_date:
        raise RuntimeError("nightly scrape did not provide expected_data_date")

    data_date_min = scrape_summary.get("data_date_min")
    data_date_max = scrape_summary.get("data_date_max")
    if data_date_min != target_data_date or data_date_max != target_data_date:
        raise RuntimeError(
            "scrape data date range does not match target: "
            f"expected={target_data_date}, range={data_date_min or 'unknown'}~"
            f"{data_date_max or 'unknown'}"
        )

    persisted_date = get_latest_valid_date()
    if persisted_date != target_data_date:
        resolved_db = ":memory:" if db_path == ":memory:" else str(Path(db_path).resolve())
        raise RuntimeError(
            "persisted holdings date mismatch: "
            f"expected={target_data_date}, latest_valid={persisted_date or 'none'}, "
            f"db={resolved_db}"
        )
    return target_data_date


def _require_successful_change_detection(change_summary, target_data_date):
    if (
        change_summary.get("ok") is not True
        or change_summary.get("date") != target_data_date
    ):
        raise RuntimeError(
            "holding change detection failed for target date "
            f"{target_data_date}: {change_summary.get('reason') or change_summary}"
        )


def run_nightly_pipeline(
    db_path,
    report_dir,
    *,
    skip_discovery=False,
    strict_discovery=False,
):
    """Run the normal seven-stage nightly workflow against the supplied paths."""
    db.init_db(db_path)

    if not skip_discovery:
        print("Step 1/7: Discovering active ETF universe...")
        try:
            discovery_summary = discover_and_reconcile(db_path)
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
                if strict_discovery:
                    raise RuntimeError(message)
        except Exception as exc:
            print(f"⚠️ ETF universe discovery failed: {exc}")
            print("  Continuing with existing DB-backed ETF universe")
            if strict_discovery:
                raise
    else:
        print("Step 1/7: Skipping ETF universe discovery")

    print("Step 2/7: Running browser-enabled scrape...")
    scrape_summary = run_daily_scrape_with_browser(db_path)
    print(f"  Scrape summary: {scrape_summary}")

    if scrape_summary.get("skip_reason") == "tw_stock_market_closed":
        print(
            "TW stock market closed on "
            f"{scrape_summary.get('date')}; "
            f"latest trading data date is "
            f"{scrape_summary.get('expected_data_date') or 'unknown'}."
        )
        print("Skipping downstream steps because no new holdings data is expected.")
        print("Nightly Taiwan active ETF pipeline complete")
        return {
            "scrape_summary": scrape_summary,
            "skipped_downstream": True,
        }

    total_etfs = scrape_summary.get("total_etfs")
    moneydj_success = scrape_summary.get("moneydj_success", 0)
    official_success = scrape_summary.get("official_success", 0)
    successful_etfs = moneydj_success + official_success
    available_etfs = (
        successful_etfs + scrape_summary.get("preexisting_success", 0)
    )
    if total_etfs is not None and available_etfs < total_etfs:
        failures = scrape_summary.get("failures", [])
        failed_codes = [
            failure.get("etf_code")
            for failure in failures
            if isinstance(failure, dict) and failure.get("etf_code")
        ]
        failure_text = f"（失敗: {', '.join(failed_codes)}）" if failed_codes else ""
        print(
            f"⚠️ 資料不完整: 預期 {total_etfs} 檔 ETF，"
            f"實際可用 {available_etfs} 檔{failure_text}"
        )

    moneydj_warnings = scrape_summary.get("moneydj_warnings", [])
    if moneydj_warnings:
        print(f"\n⚠️ MoneyDJ 驗證失敗 ({len(moneydj_warnings)} ETFs):")
        for warning in moneydj_warnings:
            print(
                f"  - {warning['etf_code']} ({warning['issuer']}): "
                f"{warning['reason']}"
            )
            print(f"    Rows: {warning['rows']}, Weight: {warning['weight']:.2f}%")
            print(f"    URL: {warning['url']}")

    freshness = scrape_summary.get("data_freshness") or {}
    fresh = freshness.get("fresh", 0)
    stale = freshness.get("stale", 0)
    unknown = freshness.get("unknown", 0)
    if fresh or stale or unknown:
        print(f"\nData freshness: fresh {fresh} / stale {stale} / unknown {unknown}")
        if scrape_summary.get("data_date_min") or scrape_summary.get("data_date_max"):
            print(
                "Data date range: "
                f"{scrape_summary.get('data_date_min') or 'unknown'} ~ "
                f"{scrape_summary.get('data_date_max') or 'unknown'}"
            )
        if stale > 0:
            print(
                f"PROVISIONAL REPORT: {fresh}/{total_etfs or '?'} ETFs have "
                f"{scrape_summary.get('expected_data_date') or scrape_summary.get('date')} data"
            )
            for item in scrape_summary.get("stale_etfs", [])[:10]:
                print(f"  stale: {item.get('etf_code')} data_date={item.get('data_date')}")
        if unknown > 0:
            unknown_codes = [
                item.get("etf_code")
                for item in scrape_summary.get("unknown_date_etfs", [])
                if item.get("etf_code")
            ]
            print(f"Unknown source dates: {', '.join(unknown_codes)}")

    target_data_date = _resolve_target_data_date(scrape_summary, db_path)

    print("Step 3/7: Detecting holding changes...")
    change_summary = detect_holding_changes(current_date=target_data_date)
    print(f"  Change summary: {change_summary}")
    _require_successful_change_detection(change_summary, target_data_date)

    skipped_etfs = change_summary.get("skipped_etfs", [])
    if skipped_etfs:
        print(f"⚠️ 變更偵測跳過以下 ETF: {', '.join(skipped_etfs)}")

    print("Step 4/7: Generating manager intent rollups...")
    # Note for the future report PR: primary_intent_state can be
    # cross_fund_rotation_accumulation, cross_fund_rotation_distribution, or
    # bare cross_fund_rotation when same-issuer rotation exists but net direction
    # is unclear. Report text should present the bare state as unclear/mandate
    # rotation, not as accumulation or distribution.
    manager_intent_summary = generate_manager_intent_rollups(target_data_date)
    print(f"  Manager intent summary: {manager_intent_summary}")

    print("Step 5/7: Generating manager signals...")
    signal_summary = generate_manager_signals(target_data_date)
    print(f"  Signal summary: {signal_summary}")

    print("Step 6/7: Generating signal report...")
    report_text = generate_signal_report(
        target_data_date,
        quality_run_date=scrape_summary.get("date"),
    )

    report_dir_path = Path(report_dir)
    report_dir_path.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_date = target_data_date
    report_path = report_dir_path / f"taiwan_active_etf_signal_report_{report_date}.txt"
    report_archive_path = (
        report_dir_path / f"taiwan_active_etf_signal_report_{stamp}.txt"
    )
    report_path.write_text(report_text, encoding="utf-8")
    report_archive_path.write_text(report_text, encoding="utf-8")

    print("Step 7/7: Generating traction analysis (raw data)...")
    traction_path = None
    traction_archive_path = None
    traction_raw = None
    try:
        traction_raw = generate_traction_report(
            db_path=db_path,
            window_days=10,
        )
        traction_path = report_dir_path / f"traction_raw_{report_date}.txt"
        traction_archive_path = report_dir_path / f"traction_raw_{stamp}.txt"
        traction_path.write_text(traction_raw, encoding="utf-8")
        traction_archive_path.write_text(traction_raw, encoding="utf-8")
        print(f"Traction raw data written to: {traction_path}")
        print(f"Traction raw archive written to: {traction_archive_path}")
    except Exception as exc:
        print(f"⚠️ Traction analysis failed (non-fatal): {exc}")

    print("Nightly Taiwan active ETF pipeline complete")
    print(f"Signal report: {report_path}")
    print(f"Signal report archive: {report_archive_path}")
    if traction_path:
        print(f"Traction data: {traction_path}")
    if traction_archive_path:
        print(f"Traction data archive: {traction_archive_path}")

    return {
        "scrape_summary": scrape_summary,
        "change_summary": change_summary,
        "manager_intent_summary": manager_intent_summary,
        "signal_summary": signal_summary,
        "signal_report": report_text,
        "traction_report": traction_raw,
        "report_path": str(report_path),
        "report_archive_path": str(report_archive_path),
        "traction_path": str(traction_path) if traction_path else None,
        "traction_archive_path": (
            str(traction_archive_path) if traction_archive_path else None
        ),
    }


if __name__ == "__main__":
    main()
