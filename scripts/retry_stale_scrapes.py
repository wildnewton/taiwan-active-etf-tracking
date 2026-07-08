"""Retry stale ETF scrape rows for a single report date.

The command re-scrapes stale rows for the requested report date, then reruns
same-date derived layers and overwrites the date-only primary reports only when
at least one retried ETF becomes fresh.
"""
import argparse
import json
from pathlib import Path

import db
from changes import detect_holding_changes
from manager_intent import generate_manager_intent_rollups
from pipeline import run_selected_scrape_with_browser
from report import generate_signal_report
from signals import generate_manager_signals
from traction_analysis import generate_traction_report


def get_stale_scrape_runs(run_date: str) -> list[dict]:
    """Return active ETFs whose successful scrape rows are older than run_date."""
    with db._connect() as conn:
        rows = conn.execute(
            """
            SELECT sr.etf_code, sr.data_date
            FROM etf_scrape_runs sr
            JOIN etf_universe u ON sr.etf_code = u.code
            WHERE sr.date = ?
              AND sr.status = 'success'
              AND sr.data_date IS NOT NULL
              AND sr.data_date < ?
              AND u.retired = 0
            ORDER BY sr.etf_code
            """,
            (run_date, run_date),
        ).fetchall()
    return [{"etf_code": row[0], "data_date": row[1]} for row in rows]


def stale_count(run_date: str) -> int:
    return len(get_stale_scrape_runs(run_date))


def retry_stale_etfs(
    db_path: str = "data/active_etf_holdings.sqlite",
    run_date: str | None = None,
    report_dir: str | Path = "reports",
) -> dict:
    db.init_db(db_path)
    run_date = run_date or _latest_scrape_run_date()
    if not run_date:
        return _empty_summary(run_date)

    stale_rows = get_stale_scrape_runs(run_date)
    stale_before = len(stale_rows)
    if stale_before == 0:
        return {
            "date": run_date,
            "retried_etfs": [],
            "stale_before": 0,
            "stale_after": 0,
            "improved": False,
            "reports_overwritten": False,
        }

    etf_codes = [row["etf_code"] for row in stale_rows]
    retry_summary = run_selected_scrape_with_browser(db_path, etf_codes, run_date=run_date)
    fresh_after_retry = retry_summary.get("data_freshness", {}).get("fresh", 0)
    stale_after = max(stale_before - fresh_after_retry, 0)
    improved = stale_after < stale_before

    summary = {
        "date": run_date,
        "retried_etfs": etf_codes,
        "stale_before": stale_before,
        "stale_after": stale_after,
        "improved": improved,
        "reports_overwritten": False,
        "retry_summary": retry_summary,
    }
    if not improved:
        return summary

    change_summary = detect_holding_changes(current_date=run_date)
    intent_summary = generate_manager_intent_rollups(run_date)
    signal_summary = generate_manager_signals()
    report_paths = _overwrite_reports(db_path, run_date, report_dir)

    summary.update({
        "reports_overwritten": True,
        "change_summary": change_summary,
        "manager_intent_summary": intent_summary,
        "signal_summary": signal_summary,
        "report_paths": report_paths,
    })
    return summary


def _overwrite_reports(db_path: str, run_date: str, report_dir: str | Path) -> dict:
    """Overwrite date-only primary reports after successful same-date retry.

    The nightly pipeline writes these same date-only primary filenames plus
    timestamped archives. Retry intentionally overwrites only the primary files;
    it does not create another archive.
    """
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    signal_text = generate_signal_report(run_date)
    traction_text = generate_traction_report(db_path=db_path, window_days=10)

    signal_path = report_dir / f"taiwan_active_etf_signal_report_{run_date}.txt"
    traction_path = report_dir / f"traction_raw_{run_date}.txt"
    signal_path.write_text(signal_text, encoding="utf-8")
    traction_path.write_text(traction_text, encoding="utf-8")
    return {"signal_report": str(signal_path), "traction_report": str(traction_path)}


def _latest_scrape_run_date() -> str | None:
    with db._connect() as conn:
        row = conn.execute("SELECT MAX(date) FROM etf_scrape_runs").fetchone()
    return row[0] if row and row[0] else None


def _empty_summary(run_date: str | None) -> dict:
    return {
        "date": run_date,
        "retried_etfs": [],
        "stale_before": 0,
        "stale_after": 0,
        "improved": False,
        "reports_overwritten": False,
    }


def main():
    parser = argparse.ArgumentParser(description="Retry stale ETF scrape rows")
    parser.add_argument("--db", default="data/active_etf_holdings.sqlite")
    parser.add_argument("--date", dest="run_date")
    parser.add_argument("--report-dir", default="reports")
    args = parser.parse_args()

    summary = retry_stale_etfs(db_path=args.db, run_date=args.run_date, report_dir=args.report_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
