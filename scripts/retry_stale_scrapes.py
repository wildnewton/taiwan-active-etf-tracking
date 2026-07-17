"""Retry ETFs missing a persisted holdings snapshot for one target date."""
import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import db
from changes import detect_holding_changes
from manager_intent import generate_manager_intent_rollups
from pipeline import run_selected_scrape_with_browser
from report import generate_signal_report
from signals import generate_manager_signals
from traction_analysis import generate_traction_report


TAIPEI_TIMEZONE = ZoneInfo("Asia/Taipei")


def get_retry_candidates(target_date: str) -> list[dict]:
    """Return eligible ETFs without a holdings snapshot on ``target_date``."""
    coverage = db.get_target_snapshot_coverage(target_date)
    return [
        {
            "etf_code": etf_code,
            "data_date": coverage["latest_available_dates"].get(etf_code),
        }
        for etf_code in coverage["missing_etfs"]
    ]


def retry_candidate_count(target_date: str) -> int:
    return len(get_retry_candidates(target_date))


def retry_missing_holdings(
    db_path: str = "data/active_etf_holdings.sqlite",
    target_date: str | None = None,
    report_dir: str | Path = "reports",
) -> dict:
    db.init_db(db_path)
    if not target_date:
        raise ValueError("target_date is required")

    candidates = get_retry_candidates(target_date)
    missing_before = len(candidates)
    if missing_before == 0:
        return _empty_summary(
            target_date,
            datetime.now(TAIPEI_TIMEZONE).date().isoformat(),
        )

    etf_codes = [row["etf_code"] for row in candidates]
    retry_summary = run_selected_scrape_with_browser(
        db_path,
        etf_codes,
        target_date=target_date,
    )
    remaining = get_retry_candidates(target_date)
    remaining_codes = {row["etf_code"] for row in remaining}
    improved_etfs = sorted(set(etf_codes) - remaining_codes)
    improved = bool(improved_etfs)
    run_date = (
        retry_summary.get("date")
        or datetime.now(TAIPEI_TIMEZONE).date().isoformat()
    )

    summary = {
        "run_date": run_date,
        "target_date": target_date,
        "retried_etfs": etf_codes,
        "improved_etfs": improved_etfs,
        "missing_before": missing_before,
        "missing_after": len(remaining),
        "improved": improved,
        "reports_overwritten": False,
        "retry_summary": retry_summary,
    }
    if not improved:
        return summary

    change_summary = detect_holding_changes(current_date=target_date)
    if change_summary.get("ok") is not True or change_summary.get("date") != target_date:
        raise RuntimeError(
            "holding change detection failed for retry date "
            f"{target_date}: {change_summary.get('reason') or change_summary}"
        )

    intent_summary = generate_manager_intent_rollups(target_date)
    signal_summary = generate_manager_signals(target_date)
    quality_run_date = run_date
    report_paths = _overwrite_reports(
        db_path,
        target_date,
        report_dir,
        quality_run_date=quality_run_date,
    )

    summary.update({
        "reports_overwritten": True,
        "change_summary": change_summary,
        "manager_intent_summary": intent_summary,
        "signal_summary": signal_summary,
        "report_paths": report_paths,
    })
    return summary


def _overwrite_reports(
    db_path: str,
    target_date: str,
    report_dir: str | Path,
    *,
    quality_run_date: str | None = None,
) -> dict:
    """Overwrite date-only primary reports after target holdings improve."""
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    signal_text = generate_signal_report(
        target_date,
        quality_run_date=quality_run_date,
    )
    traction_text = generate_traction_report(
        db_path=db_path,
        window_days=10,
        latest_date=target_date,
    )

    signal_path = report_dir / f"taiwan_active_etf_signal_report_{target_date}.txt"
    traction_path = report_dir / f"traction_raw_{target_date}.txt"
    signal_path.write_text(signal_text, encoding="utf-8")
    traction_path.write_text(traction_text, encoding="utf-8")
    return {"signal_report": str(signal_path), "traction_report": str(traction_path)}


def _empty_summary(target_date: str, run_date: str) -> dict:
    return {
        "run_date": run_date,
        "target_date": target_date,
        "retried_etfs": [],
        "missing_before": 0,
        "missing_after": 0,
        "improved": False,
        "reports_overwritten": False,
    }


def main():
    parser = argparse.ArgumentParser(description="Retry ETFs missing target-date holdings")
    parser.add_argument("--db", default="data/active_etf_holdings.sqlite")
    parser.add_argument("--date", dest="target_date", required=True)
    parser.add_argument("--report-dir", default="reports")
    args = parser.parse_args()

    summary = retry_missing_holdings(
        db_path=args.db,
        target_date=args.target_date,
        report_dir=args.report_dir,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
