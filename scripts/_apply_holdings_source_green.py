from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def write(path, text):
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def replace_between(text, start, end, replacement, label):
    start_index = text.find(start)
    end_index = text.find(end, start_index + len(start))
    if start_index < 0 or end_index < 0:
        raise RuntimeError(f"{label}: markers not found")
    return text[:start_index] + replacement + text[end_index:]


# db.py: holdings/universe are the only persisted correctness state.
path = "scripts/db.py"
text = read(path)
text = replace_once(
    text,
    '''_SCRAPE_RUN_COLUMN_MIGRATIONS = {
    "data_date": "TEXT",
}

''',
    "",
    "remove scrape migrations",
)
text = replace_once(
    text,
    '''        conn.execute("CREATE TABLE IF NOT EXISTS etf_scrape_runs (date TEXT NOT NULL, data_date TEXT, etf_code TEXT NOT NULL, status TEXT NOT NULL, primary_source TEXT NOT NULL, primary_success INTEGER NOT NULL, moneydj_browser_used INTEGER NOT NULL, official_fallback_used INTEGER NOT NULL, official_success INTEGER NOT NULL, rows_extracted INTEGER NOT NULL, stock_rows_extracted INTEGER NOT NULL, non_stock_rows_extracted INTEGER NOT NULL, total_weight_all_rows REAL NOT NULL, total_weight_stock_rows REAL NOT NULL, source_url TEXT, error TEXT, started_at TEXT NOT NULL, finished_at TEXT, PRIMARY KEY (date, etf_code))")
        _ensure_scrape_run_columns(conn)
''',
    '''        # Scrape attempts are operational logs, not canonical business data.
        # Remove the legacy hybrid state table during normal DB initialization.
        conn.execute("DROP TABLE IF EXISTS etf_scrape_runs")
''',
    "drop legacy table",
)
text = replace_between(
    text,
    "def _ensure_scrape_run_columns(conn):\n",
    "def _ensure_change_diagnostics_table(conn):\n",
    "def _ensure_change_diagnostics_table(conn):\n",
    "remove scrape column migration helper",
)
coverage_helpers = '''def get_eligible_etf_codes(as_of_date):
    """Return ETFs that belonged to the tracked universe on ``as_of_date``."""
    as_of_date = _serialize(as_of_date)
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT code
            FROM etf_universe
            WHERE (listing_date IS NULL OR listing_date <= ?)
              AND (
                  retired = 0
                  OR (last_active_date IS NOT NULL AND ? <= last_active_date)
              )
            ORDER BY code
            """,
            (as_of_date, as_of_date),
        ).fetchall()
    return [row[0] for row in rows]


def get_snapshot_etf_codes(data_date):
    """Return ETFs with any persisted holdings snapshot on ``data_date``."""
    data_date = _serialize(data_date)
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT etf_code FROM etf_daily_holdings WHERE date = ?
            UNION
            SELECT etf_code FROM etf_daily_non_stock_assets WHERE date = ?
            ORDER BY etf_code
            """,
            (data_date, data_date),
        ).fetchall()
    return [row[0] for row in rows]


def get_latest_snapshot_date(etf_code, before_date=None):
    """Return the latest persisted snapshot date, optionally before a target."""
    params = [etf_code, etf_code]
    filters = ""
    if before_date is not None:
        before_date = _serialize(before_date)
        filters = "WHERE date < ?"
        params.extend([before_date, before_date])
    with _connect() as conn:
        row = conn.execute(
            f"""
            SELECT MAX(date)
            FROM (
                SELECT date FROM etf_daily_holdings
                WHERE etf_code = ? {"AND date < ?" if before_date is not None else ""}
                UNION
                SELECT date FROM etf_daily_non_stock_assets
                WHERE etf_code = ? {"AND date < ?" if before_date is not None else ""}
            )
            """,
            params,
        ).fetchone()
    return row[0] if row and row[0] else None


def get_target_snapshot_coverage(data_date):
    """Return persisted holdings coverage for one candidate data date."""
    data_date = _serialize(data_date)
    expected = set(get_eligible_etf_codes(data_date))
    persisted = set(get_snapshot_etf_codes(data_date))
    actual = persisted & expected if expected else persisted
    missing = sorted(expected - actual)
    latest_available = {
        etf_code: get_latest_snapshot_date(etf_code, before_date=data_date)
        for etf_code in missing
    }
    return {
        "date": data_date,
        "expected_etf_codes": sorted(expected),
        "actual_etf_codes": sorted(actual),
        "missing_etfs": missing,
        "latest_available_dates": latest_available,
        "expected_count": len(expected),
        "actual_count": len(actual),
    }


'''
text = replace_between(
    text,
    "def successful_snapshot_exists(date_value, etf_code):\n",
    "def _snapshot_key(rows):\n",
    coverage_helpers + "def _snapshot_key(rows):\n",
    "replace successful snapshot helper",
)
text = replace_between(
    text,
    '_USABLE_SCRAPE_STATUSES = {"success", "stale"}\n',
    "def get_last_scrape_date(etf_code):\n",
    "",
    "remove scrape persistence helpers",
)
# get_last_scrape_date is the final function in the file.
last_marker = "def get_last_scrape_date(etf_code):\n"
index = text.find(last_marker)
if index < 0:
    raise RuntimeError("remove get_last_scrape_date: marker not found")
text = text[:index].rstrip() + "\n"
write(path, text)


# models.py: remove obsolete attempt-state model.
path = "scripts/models.py"
text = read(path)
marker = "\n\n@dataclass\nclass ScrapeRun:\n"
index = text.find(marker)
if index < 0:
    raise RuntimeError("ScrapeRun marker not found")
text = text[:index].rstrip() + "\n"
write(path, text)


# pipeline.py: classify only in the in-memory summary and persist holdings only.
path = "scripts/pipeline.py"
text = read(path)
text = replace_once(
    text,
    '''from db import (
    init_db,
    insert_scrape_run,
    replace_daily_snapshot,
    snapshot_exists,
    successful_snapshot_exists,
)
''',
    '''from db import init_db, replace_daily_snapshot, snapshot_exists
''',
    "pipeline db imports",
)
text = replace_once(
    text,
    "from models import HoldingRow, NonStockAssetRow, ScrapeRun\n",
    "from models import HoldingRow, NonStockAssetRow\n",
    "pipeline model import",
)
text = replace_once(
    text,
    '            if successful_snapshot_exists(expected_data_date, etf["code"])\n',
    '            if snapshot_exists(expected_data_date, etf["code"])\n',
    "preexisting snapshot predicate",
)
new_sync = '''def _execute_scrape_sync(
    etfs: list[dict],
    scrape_fn: ScrapeFn,
    run_date: date,
    expected_data_date: Optional[date],
    summary: dict,
) -> dict:
    freshness_target_date = expected_data_date or run_date
    for etf in etfs:
        etf_code = etf["code"]
        result = scrape_fn(etf_code, freshness_target_date)
        _record_result(summary, etf_code, run_date, expected_data_date, result)

    _finalize_data_date_range(summary)
    return summary


'''
text = replace_between(
    text,
    "def _execute_scrape_sync(\n",
    "async def _execute_scrape_async(\n",
    new_sync + "async def _execute_scrape_async(\n",
    "sync scrape executor",
)
new_async = '''async def _execute_scrape_async(
    etfs: list[dict],
    scrape_fn: AsyncScrapeFn,
    run_date: date,
    expected_data_date: Optional[date],
    summary: dict,
) -> dict:
    freshness_target_date = expected_data_date or run_date
    for etf in etfs:
        etf_code = etf["code"]
        result = await scrape_fn(etf_code, freshness_target_date)
        _record_result(summary, etf_code, run_date, expected_data_date, result)

    _finalize_data_date_range(summary)
    return summary


'''
text = replace_between(
    text,
    "async def _execute_scrape_async(\n",
    "async def _execute_scrape_async_with_pages(\n",
    new_async + "async def _execute_scrape_async_with_pages(\n",
    "async scrape executor",
)
new_pages = '''async def _execute_scrape_async_with_pages(
    etfs: list[dict],
    context,
    run_date: date,
    expected_data_date: Optional[date],
    summary: dict,
) -> dict:
    """Scrape concurrently, then record sequentially in ETF input order."""
    freshness_target_date = expected_data_date or run_date
    semaphore = asyncio.Semaphore(_ASYNC_SCRAPE_CONCURRENCY)

    async def scrape_one(etf: dict):
        etf_code = etf["code"]
        async with semaphore:
            page = None
            try:
                page = await context.new_page()
                result = await scrape_holdings_with_browser_async(
                    etf_code,
                    page,
                    target_date=freshness_target_date,
                )
            except Exception as exc:
                result = {
                    **FAILED_RESULT,
                    "reason": f"unhandled scraper exception: {exc}",
                }
            finally:
                if page is not None:
                    try:
                        await page.close()
                    except Exception as exc:
                        close_reason = f"unhandled page close exception: {exc}"
                        if result.get("ok") is False and result.get("reason"):
                            result = {
                                **result,
                                "reason": f"{result['reason']}; {close_reason}",
                            }
                        else:
                            result = {
                                **FAILED_RESULT,
                                "reason": close_reason,
                            }
            return etf_code, result

    outcomes = await asyncio.gather(*(scrape_one(etf) for etf in etfs))
    for etf_code, result in outcomes:
        _record_result(summary, etf_code, run_date, expected_data_date, result)

    _finalize_data_date_range(summary)
    return summary


'''
text = replace_between(
    text,
    "async def _execute_scrape_async_with_pages(\n",
    "def _current_run_at() -> datetime:\n",
    new_pages + "def _current_run_at() -> datetime:\n",
    "page scrape executor",
)
text = text.replace(
    '    """Return the final persisted status for one scrape result."""',
    '    """Classify one result for the current in-memory scrape summary."""',
)
new_record = '''def _record_result(
    summary: dict,
    etf_code: str,
    run_date: date,
    expected_data_date: Optional[date],
    result: dict,
) -> None:
    freshness_target_date = expected_data_date or run_date

    if result["ok"] is not True:
        reason = result.get("reason") or "scrape_failed"
        _record_freshness(
            summary,
            etf_code,
            "failed",
            None,
            result,
            unknown_reason=reason,
        )
        _record_failure(summary, etf_code, reason)
        return

    data_date, date_error = _validate_snapshot_dates(result)
    if date_error is not None:
        _record_freshness(
            summary,
            etf_code,
            "failed",
            None,
            result,
            unknown_reason=date_error,
        )
        _record_failure(
            summary,
            etf_code,
            date_error,
            run_moneydj_diagnostic=result.get("source_type")
            not in {"moneydj_primary", "moneydj_browser"},
        )
        return

    final_status = _classify_scrape_status(
        result,
        data_date,
        freshness_target_date,
    )
    _record_weight_warning(summary, etf_code, result)

    if final_status == "failed":
        reason = "source_date_after_expected_data_date"
        _record_freshness(
            summary,
            etf_code,
            final_status,
            data_date,
            result,
            unknown_reason=reason,
        )
        _record_failure(
            summary,
            etf_code,
            reason,
            run_moneydj_diagnostic=result.get("source_type")
            not in {"moneydj_primary", "moneydj_browser"},
        )
        return

    summary["total_stock_rows"] += len(result["stock_rows"])
    summary["total_non_stock_rows"] += len(result["non_stock_rows"])
    if result["source_type"] in {"moneydj_primary", "moneydj_browser"}:
        summary["moneydj_success"] += 1
    elif result["source_type"] == "official_fallback":
        summary["official_success"] += 1
        _check_moneydj_warning(summary, etf_code)
    _record_freshness(summary, etf_code, final_status, data_date, result)
    _record_row_count_warning(summary, etf_code, result)

    if final_status == "stale" and _should_skip_stale_existing_snapshot(
        data_date,
        etf_code,
    ):
        _record_stale_existing(summary, etf_code, data_date, result)
        return

    stock_rows = [_to_holding_row(row) for row in result["stock_rows"]]
    non_stock_rows = [
        _to_non_stock_asset_row(row) for row in result["non_stock_rows"]
    ]
    replace_daily_snapshot(stock_rows, non_stock_rows)


'''
text = replace_between(
    text,
    "def _record_result(\n",
    "def _record_failure(\n",
    new_record + "def _record_failure(\n",
    "pipeline result recorder",
)
text = replace_between(
    text,
    "def _build_scrape_run(\n",
    "def _parse_row_date(value) -> Optional[date]:\n",
    "def _parse_row_date(value) -> Optional[date]:\n",
    "remove scrape run builder",
)
write(path, text)


# changes.py: candidate-date coverage is calculated from holdings + historical universe.
path = "scripts/changes.py"
text = read(path)
new_selector = '''def _get_valid_holding_date(
    before_date: Optional[str],
    min_success_ratio: float,
) -> Optional[str]:
    where_clause = "WHERE date < ?" if before_date else ""
    params = (before_date,) if before_date else ()
    with db._connect() as conn:
        rows = conn.execute(
            f"""
            SELECT date
            FROM (
                SELECT date FROM etf_daily_holdings
                UNION
                SELECT date FROM etf_daily_non_stock_assets
            )
            {where_clause}
            GROUP BY date
            """,
            params,
        ).fetchall()

    candidate_dates = sorted((row[0] for row in rows), reverse=True)
    for date_value in candidate_dates:
        coverage = db.get_target_snapshot_coverage(date_value)
        expected_count = coverage["expected_count"]
        actual_count = coverage["actual_count"]
        required_count = ceil(expected_count * min_success_ratio) if expected_count else 1
        if actual_count >= required_count:
            return date_value
    return None


'''
text = replace_between(
    text,
    "def _get_valid_holding_date(\n",
    "def detect_holding_changes(\n",
    new_selector + "def detect_holding_changes(\n",
    "valid holdings date selector",
)
write(path, text)


# report.py: quality is target holdings coverage, not scrape-attempt state.
path = "scripts/report.py"
text = read(path)
text = replace_once(
    text,
    '''    data_date = signal_date or _get_latest_holdings_date()
    quality_run_date = quality_run_date or (
        signal_date if signal_date is not None else _get_latest_scrape_run_date()
    )
''',
    '''    data_date = signal_date or _get_latest_holdings_date()
''',
    "report date initialization",
)
new_render = '''def _render_data_quality(quality: dict) -> list[str]:
    lines = ["═══ 資料品質 / 信任度 ═══"]
    lines.append(f"資料品質: {quality['status_label']}")
    if quality.get("quality_run_date"):
        lines.append(f"抓取執行日: {quality['quality_run_date']}")

    freshness = quality.get("scrape_freshness") or _empty_scrape_freshness()
    stale_rows = freshness.get("stale") or []
    unknown_rows = freshness.get("unknown") or []
    fresh_rows = freshness.get("fresh") or []
    if quality["expected_count"]:
        lines.append(
            "Active ETF universe: "
            f"{quality['expected_count']} | 成功持倉 ETF: "
            f"{quality['actual_count']}/{quality['expected_count']}"
        )
    else:
        lines.append(f"成功持倉 ETF: {quality['actual_count']}")

    denominator = quality["expected_count"] or len(fresh_rows)
    if denominator or stale_rows or unknown_rows:
        lines.append(
            f"資料新鮮度: fresh {len(fresh_rows)}/{denominator} | "
            f"stale {len(stale_rows)} | unknown {len(unknown_rows)}"
        )
    if quality.get("missing_etfs"):
        lines.append(
            "報告狀態: ⚠️ Provisional / 暫定（部分 ETF 缺少目標日持倉；避免全體化結論）"
        )
        lines.append(f"缺少目標日持倉: {', '.join(quality['missing_etfs'])}")
    if stale_rows:
        lines.append("最近可用資料日期:")
        for row in stale_rows:
            lines.append(f"  {row['etf_code']} source {row.get('data_date') or 'N/A'}")
    if unknown_rows:
        lines.append("無歷史持倉資料:")
        for row in unknown_rows:
            lines.append(f"  {row['etf_code']}")
    if quality.get("change_skips"):
        lines.append("變更偵測跳過:")
        for row in quality["change_skips"]:
            current_source = row.get("current_source_type") or "None"
            previous_source = row.get("previous_source_type") or "None"
            lines.append(
                f"  {row['etf_code']} {row.get('reason') or 'unknown'} "
                f"({current_source}→{previous_source})"
            )
    if quality["warnings"]:
        lines.append("資料品質警告:")
        for warning in quality["warnings"]:
            lines.append(f"  {warning}")
    else:
        lines.append("資料品質警告: 無")
    lines.append("")
    return lines


'''
text = replace_between(
    text,
    "def _render_data_quality(quality: dict) -> list[str]:\n",
    "def _render_manager_signals(signals: list[dict]) -> list[str]:\n",
    new_render + "def _render_manager_signals(signals: list[dict]) -> list[str]:\n",
    "report quality renderer",
)
text = replace_between(
    text,
    "def _get_latest_scrape_run_date():\n",
    "def _get_previous_holdings_date(current_date):\n",
    "def _get_previous_holdings_date(current_date):\n",
    "remove latest scrape run lookup",
)
new_quality = '''def _empty_scrape_freshness():
    return {"fresh": [], "stale": [], "unknown": []}


def _get_data_quality(data_date, quality_run_date=None):
    if not data_date:
        return {
            "status_label": "❌ No data",
            "quality_run_date": quality_run_date,
            "expected_count": get_active_etf_count(),
            "actual_count": 0,
            "missing_etfs": [],
            "change_skips": [],
            "scrape_freshness": _empty_scrape_freshness(),
            "warnings": ["⚠️ 無持倉資料"],
        }

    coverage = db.get_target_snapshot_coverage(data_date)
    freshness = {
        "fresh": [
            {"etf_code": code, "data_date": data_date}
            for code in coverage["actual_etf_codes"]
        ],
        "stale": [],
        "unknown": [],
    }
    for code in coverage["missing_etfs"]:
        latest_date = coverage["latest_available_dates"].get(code)
        item = {"etf_code": code, "data_date": latest_date}
        freshness["stale" if latest_date else "unknown"].append(item)

    change_skips = _get_skipped_change_diagnostics(data_date)
    warnings = _get_data_warnings(data_date)
    degraded = bool(coverage["missing_etfs"] or warnings or change_skips)
    return {
        "status_label": "⚠️ Degraded" if degraded else "✅ Clean",
        "quality_run_date": quality_run_date,
        "expected_count": coverage["expected_count"],
        "actual_count": coverage["actual_count"],
        "missing_etfs": coverage["missing_etfs"],
        "latest_available_dates": coverage["latest_available_dates"],
        "change_skips": change_skips,
        "scrape_freshness": freshness,
        "warnings": warnings,
    }


'''
text = replace_between(
    text,
    "def _empty_scrape_freshness():\n",
    "def _get_skipped_change_diagnostics(data_date):\n",
    new_quality + "def _get_skipped_change_diagnostics(data_date):\n",
    "report holdings quality",
)
write(path, text)


# nightly_pipeline.py: readiness is persisted target coverage, never summary status.
path = "scripts/nightly_pipeline.py"
text = read(path)
new_resolver = '''def _resolve_target_data_date(scrape_summary, db_path):
    """Validate persisted holdings coverage for the expected target date."""
    target_data_date = scrape_summary.get("expected_data_date")
    if not target_data_date:
        raise RuntimeError("nightly scrape did not provide expected_data_date")

    coverage = db.get_target_snapshot_coverage(target_data_date)
    persisted_date = get_latest_valid_date()
    if persisted_date != target_data_date:
        resolved_db = ":memory:" if db_path == ":memory:" else str(Path(db_path).resolve())
        raise RuntimeError(
            "persisted holdings date mismatch: "
            f"expected={target_data_date}, latest_valid={persisted_date or 'none'}, "
            f"coverage={coverage['actual_count']}/{coverage['expected_count'] or '?'}, "
            f"missing={','.join(coverage['missing_etfs']) or 'none'}, db={resolved_db}"
        )
    return target_data_date


'''
text = replace_between(
    text,
    "def _resolve_target_data_date(scrape_summary, db_path):\n",
    "def _require_successful_change_detection(change_summary, target_data_date):\n",
    new_resolver + "def _require_successful_change_detection(change_summary, target_data_date):\n",
    "nightly target resolver",
)
write(path, text)


# retry_stale_scrapes.py: retain the operational filename, but retry target gaps.
write(
    "scripts/retry_stale_scrapes.py",
    '''"""Retry ETFs missing a persisted holdings snapshot for one target date."""
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
        return _empty_summary(target_date)

    etf_codes = [row["etf_code"] for row in candidates]
    retry_summary = run_selected_scrape_with_browser(
        db_path,
        etf_codes,
        run_date=target_date,
    )
    remaining = get_retry_candidates(target_date)
    remaining_codes = {row["etf_code"] for row in remaining}
    improved_etfs = sorted(set(etf_codes) - remaining_codes)
    improved = bool(improved_etfs)

    summary = {
        "date": target_date,
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
    quality_run_date = retry_summary.get("date") or target_date
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


def _empty_summary(target_date: str) -> dict:
    return {
        "date": target_date,
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
''',
)


# README: describe holdings-gap watchdog and canonical data.
path = "README.md"
text = read(path)
text = text.replace(
    "The project stores operational state in SQLite and treats the `etf_universe` table as the source of truth for which ETFs should be fetched. Rows with `retired = 0` are included in nightly scraping; retired rows are retained for historical lookup.",
    "The project stores canonical ETF universe and holdings snapshots in SQLite. Holdings tables are the source of truth for data completeness and retry decisions; scrape-attempt status is not persisted.",
)
text = text.replace("targeted stale-ETF retry workflow", "target-date holdings-gap retry workflow")
text = text.replace("## 21:00 stale-data watchdog", "## 21:00 holdings-gap watchdog")
text = text.replace(
    "After the 20:00 report job, the 21:00 watchdog should retry only stale ETFs for that report date. It should not re-scrape the full universe.",
    "After the report job, the watchdog retries only eligible ETFs that still lack a persisted holdings snapshot for the target date. It does not re-scrape the full universe.",
)
text = text.replace(
    "- retry only stale ETFs selected by `scripts/retry_stale_scrapes.py`\n- treat the report as provisional while `data_freshness.stale > 0` or `stale_etfs` is non-empty\n- distinguish stale `data_date` from unknown `data_date`\n- overwrite date-only primary reports only after improvement\n- do not make all-universe claims when freshness is partial",
    "- retry only target-date holdings gaps selected by `scripts/retry_stale_scrapes.py`\n- keep failed retries eligible until the exact target snapshot exists\n- distinguish a prior available snapshot from no historical snapshot\n- overwrite date-only primary reports only after holdings coverage improves\n- do not make all-universe claims when target coverage is partial",
)
text = text.replace(
    "- `scripts/retry_stale_scrapes.py`: retries stale ETF scrape rows for one report date and overwrites date-only primary reports only after freshness improves.",
    "- `scripts/retry_stale_scrapes.py`: retries eligible ETFs missing target-date holdings and overwrites date-only primary reports only after coverage improves.",
)
write(path, text)


# Tests: remove obsolete persisted-state suites and update direct contracts.
for obsolete in [
    "tests/test_scrape_run_preservation.py",
    "tests/test_stale_scrape_status.py",
    "tests/test_stale_status_review_followups.py",
    "tests/test_cutoff_freshness_consumers.py",
    "tests/test_successful_snapshot_eligibility.py",
]:
    target = ROOT / obsolete
    if target.exists():
        target.unlink()

path = "tests/conftest.py"
text = read(path).replace("pipeline.successful_snapshot_exists", "pipeline.snapshot_exists")
write(path, text)

write(
    "tests/test_retry_stale_scrapes.py",
    '''from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import db
import retry_stale_scrapes
from models import HoldingRow


def _seed_universe(code, *, listing_date="2026-07-01", retired=0, last_active_date=None):
    now = datetime.now().isoformat()
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_universe (
                code, name, listing_date, retired, first_seen_date,
                last_active_date, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (code, code, listing_date, retired, listing_date, last_active_date, now, now),
        )


def _holding(data_date, etf_code):
    db.insert_holdings([
        HoldingRow(
            date=date.fromisoformat(data_date),
            etf_code=etf_code,
            asset_name="Stock 2330",
            asset_type="stock",
            stock_code="2330",
            stock_name="Stock 2330",
            shares=100,
            weight_pct=10,
            source_url="https://example.com",
            source_type="moneydj_primary",
            extraction_method="test",
            scraped_at=datetime.now(),
        )
    ])


def test_retry_candidates_are_target_holdings_gaps(tmp_path):
    db.init_db(tmp_path / "holdings.sqlite")
    _seed_universe("A")
    _seed_universe("B")
    _seed_universe("FUTURE", listing_date="2026-07-20")
    _seed_universe("RETIRED", retired=1, last_active_date="2026-07-14")
    _holding("2026-07-14", "A")
    _holding("2026-07-15", "B")

    assert retry_stale_scrapes.get_retry_candidates("2026-07-15") == [
        {"etf_code": "A", "data_date": "2026-07-14"}
    ]


def test_failed_retry_remains_eligible(tmp_path):
    db_path = tmp_path / "holdings.sqlite"
    db.init_db(db_path)
    _seed_universe("A")
    _holding("2026-07-14", "A")

    with patch.object(
        retry_stale_scrapes,
        "run_selected_scrape_with_browser",
        return_value={"date": "2026-07-15", "failed": 1},
    ):
        summary = retry_stale_scrapes.retry_missing_holdings(
            str(db_path),
            target_date="2026-07-15",
            report_dir=tmp_path / "reports",
        )

    assert summary["missing_before"] == 1
    assert summary["missing_after"] == 1
    assert summary["improved"] is False
    assert retry_stale_scrapes.get_retry_candidates("2026-07-15") == [
        {"etf_code": "A", "data_date": "2026-07-14"}
    ]


def test_successful_retry_rebuilds_same_target_date(tmp_path):
    db_path = tmp_path / "holdings.sqlite"
    db.init_db(db_path)
    _seed_universe("A")
    _holding("2026-07-14", "A")

    def complete_target(*_args, **_kwargs):
        _holding("2026-07-15", "A")
        return {"date": "2026-07-15", "failed": 0}

    with patch.object(
        retry_stale_scrapes,
        "run_selected_scrape_with_browser",
        side_effect=complete_target,
    ), patch.object(
        retry_stale_scrapes,
        "detect_holding_changes",
        return_value={"ok": True, "date": "2026-07-15", "rows": 1},
    ) as changes, patch.object(
        retry_stale_scrapes,
        "generate_manager_intent_rollups",
        return_value={"ok": True},
    ) as intent, patch.object(
        retry_stale_scrapes,
        "generate_manager_signals",
        return_value={"ok": True},
    ) as signals, patch.object(
        retry_stale_scrapes,
        "_overwrite_reports",
        return_value={},
    ) as reports:
        summary = retry_stale_scrapes.retry_missing_holdings(
            str(db_path),
            target_date="2026-07-15",
            report_dir=Path("reports"),
        )

    assert summary["missing_after"] == 0
    assert summary["improved_etfs"] == ["A"]
    changes.assert_called_once_with(current_date="2026-07-15")
    intent.assert_called_once_with("2026-07-15")
    signals.assert_called_once_with("2026-07-15")
    reports.assert_called_once_with(
        str(db_path),
        "2026-07-15",
        Path("reports"),
        quality_run_date="2026-07-15",
    )
''',
)

# Rewrite the date-contract tests to use persisted coverage rather than scrape status.
path = "tests/test_pipeline_date_contract.py"
text = read(path)
text = text.replace(
    '         patch.object(nightly_pipeline, "get_latest_valid_date", return_value=latest_valid_date) as latest, \\\n',
    '         patch.object(nightly_pipeline.db, "get_target_snapshot_coverage", return_value={"actual_count": 1, "expected_count": 1, "missing_etfs": []}), \\\n         patch.object(nightly_pipeline, "get_latest_valid_date", return_value=latest_valid_date) as latest, \\\n',
)
text = text.replace(
    '''def test_nightly_rejects_mixed_scrape_dates_before_change_detection(tmp_path):
    with pytest.raises(RuntimeError, match="scrape data date range"):
        _run_nightly(
            tmp_path,
            _scrape_summary(data_date_min=OLD_DATE, data_date_max=TARGET_DATE),
        )


''',
    '''def test_nightly_uses_persisted_target_coverage_not_summary_date_range(tmp_path):
    result, *_ = _run_nightly(
        tmp_path,
        _scrape_summary(data_date_min=OLD_DATE, data_date_max=TARGET_DATE),
    )
    assert result["change_summary"]["date"] == TARGET_DATE


''',
)
text = replace_between(
    text,
    "def _retry_with_improvement(run_date, change_summary):\n",
    "def test_historical_retry_traction_uses_the_explicit_retry_date(tmp_path):\n",
    '''def _retry_with_improvement(target_date, change_summary):
    with patch.object(retry_stale_scrapes.db, "init_db"), \\
         patch.object(
             retry_stale_scrapes,
             "get_retry_candidates",
             side_effect=[
                 [{"etf_code": "00401A", "data_date": OLD_DATE}],
                 [],
             ],
         ), \\
         patch.object(retry_stale_scrapes, "run_selected_scrape_with_browser", return_value={"date": target_date}), \\
         patch.object(retry_stale_scrapes, "detect_holding_changes", return_value=change_summary) as changes, \\
         patch.object(retry_stale_scrapes, "generate_manager_intent_rollups", return_value={}) as intent, \\
         patch.object(retry_stale_scrapes, "generate_manager_signals", return_value={}) as signals, \\
         patch.object(retry_stale_scrapes, "_overwrite_reports", return_value={}) as reports:
        result = retry_stale_scrapes.retry_missing_holdings(
            db_path=":memory:",
            target_date=target_date,
            report_dir=Path("reports"),
        )
    return result, changes, intent, signals, reports


def test_historical_retry_rebuilds_every_layer_for_the_explicit_date():
    historical_date = "2026-07-10"
    result, changes, intent, signals, reports = _retry_with_improvement(
        historical_date,
        {
            "ok": True,
            "date": historical_date,
            "previous_date": "2026-07-09",
            "rows": 1,
        },
    )

    changes.assert_called_once_with(current_date=historical_date)
    intent.assert_called_once_with(historical_date)
    signals.assert_called_once_with(historical_date)
    reports.assert_called_once_with(
        ":memory:",
        historical_date,
        Path("reports"),
        quality_run_date=historical_date,
    )
    assert result["reports_overwritten"] is True


'''
    + "def test_historical_retry_traction_uses_the_explicit_retry_date(tmp_path):\n",
    "rewrite retry date contracts",
)
text = text.replace(
    '''        retry_stale_scrapes._overwrite_reports(
            ":memory:",
            historical_date,
            tmp_path,
        )
''',
    '''        retry_stale_scrapes._overwrite_reports(
            ":memory:",
            historical_date,
            tmp_path,
            quality_run_date=historical_date,
        )
''',
)
write(path, text)

print("Applied holdings-source-of-truth GREEN changes")
