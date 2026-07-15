from pathlib import Path


def replace_function(path: str, name: str, next_name: str, replacement: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    start = text.index(f"def {name}(")
    end = text.index(f"\n\ndef {next_name}(", start)
    target.write_text(text[:start] + replacement.rstrip() + text[end:], encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


pipeline_path = Path("scripts/pipeline.py")
pipeline_text = pipeline_path.read_text(encoding="utf-8")
classifier_marker = "\n\ndef _record_result("
classifier = '''


def _classify_scrape_status(
    result: dict,
    data_date: Optional[date],
    expected_data_date: date,
) -> str:
    """Return the final persisted status for one scrape result."""
    if result["ok"] is not True or data_date is None:
        return "failed"
    if data_date < expected_data_date:
        return "stale"
    if data_date == expected_data_date:
        return "success"
    return "failed"


def _record_result('''
if classifier_marker not in pipeline_text:
    raise RuntimeError("pipeline.py: _record_result marker not found")
pipeline_path.write_text(
    pipeline_text.replace(classifier_marker, classifier, 1),
    encoding="utf-8",
)

replace_function(
    "scripts/pipeline.py",
    "_record_result",
    "_record_failure",
    '''def _record_result(
    summary: dict,
    etf_code: str,
    run_date: date,
    expected_data_date: Optional[date],
    started_at: datetime,
    finished_at: datetime,
    result: dict,
) -> None:
    should_record_scrape_run = True
    freshness_target_date = expected_data_date or run_date
    data_date = None
    final_result = result
    final_status = "failed"

    if result["ok"] is True:
        data_date, date_error = _validate_snapshot_dates(result)
        if date_error is not None:
            final_result = {**result, "ok": False, "reason": date_error}
            _record_freshness(
                summary,
                etf_code,
                freshness_target_date,
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
            insert_scrape_run(
                _build_scrape_run(
                    etf_code,
                    run_date,
                    None,
                    started_at,
                    finished_at,
                    final_result,
                    status="failed",
                )
            )
            return

        final_status = _classify_scrape_status(
            result,
            data_date,
            freshness_target_date,
        )
        _record_weight_warning(summary, etf_code, result)

        if final_status == "failed":
            reason = "source_date_after_run_date"
            final_result = {**result, "ok": False, "reason": reason}
            _record_freshness(
                summary,
                etf_code,
                freshness_target_date,
                data_date,
                result,
            )
            _record_failure(
                summary,
                etf_code,
                reason,
                run_moneydj_diagnostic=result.get("source_type")
                not in {"moneydj_primary", "moneydj_browser"},
            )
            insert_scrape_run(
                _build_scrape_run(
                    etf_code,
                    run_date,
                    data_date,
                    started_at,
                    finished_at,
                    final_result,
                    status=final_status,
                )
            )
            return

        if final_status == "stale" and _should_skip_stale_existing_snapshot(
            data_date,
            freshness_target_date,
            etf_code,
        ):
            _record_freshness(
                summary,
                etf_code,
                freshness_target_date,
                data_date,
                result,
            )
            _record_stale_existing(summary, etf_code, data_date, result)
            insert_scrape_run(
                _build_scrape_run(
                    etf_code,
                    run_date,
                    data_date,
                    started_at,
                    finished_at,
                    result,
                    status="skipped_stale_existing",
                )
            )
            return

        stock_rows = [_to_holding_row(row) for row in result["stock_rows"]]
        non_stock_rows = [
            _to_non_stock_asset_row(row) for row in result["non_stock_rows"]
        ]
        write_result = replace_daily_snapshot(stock_rows, non_stock_rows)
        should_record_scrape_run = write_result.get("inserted", False)

        summary["total_stock_rows"] += len(stock_rows)
        summary["total_non_stock_rows"] += len(non_stock_rows)
        if result["source_type"] in {"moneydj_primary", "moneydj_browser"}:
            summary["moneydj_success"] += 1
        elif result["source_type"] == "official_fallback":
            summary["official_success"] += 1
            _check_moneydj_warning(summary, etf_code)
        _record_freshness(
            summary,
            etf_code,
            freshness_target_date,
            data_date,
            result,
        )
        _record_row_count_warning(summary, etf_code, result)
    else:
        _record_failure(summary, etf_code, result["reason"])

    if should_record_scrape_run:
        insert_scrape_run(
            _build_scrape_run(
                etf_code,
                run_date,
                data_date,
                started_at,
                finished_at,
                final_result,
                status=final_status,
            )
        )''',
)

replace_function(
    "scripts/pipeline.py",
    "_record_freshness",
    "_finalize_data_date_range",
    '''def _record_freshness(
    summary: dict,
    etf_code: str,
    target_date: date,
    data_date: Optional[date],
    result: dict,
    unknown_reason: str = "missing_or_unparseable_source_date",
) -> None:
    source_type = result.get("source_type") or "unknown"
    if data_date is None:
        summary["data_freshness"]["unknown"] += 1
        summary["unknown_date_etfs"].append({
            "etf_code": etf_code,
            "source_type": source_type,
            "reason": unknown_reason,
        })
        return

    if data_date > target_date:
        summary["data_freshness"]["unknown"] += 1
        summary["unknown_date_etfs"].append({
            "etf_code": etf_code,
            "source_type": source_type,
            "reason": "source_date_after_run_date",
        })
        return

    summary["_known_data_dates"].append(data_date)
    if data_date == target_date:
        summary["data_freshness"]["fresh"] += 1
    else:
        summary["data_freshness"]["stale"] += 1
        summary["stale_etfs"].append({
            "etf_code": etf_code,
            "data_date": data_date.isoformat(),
            "source_type": source_type,
            "reason": "source_date_before_run_date",
        })''',
)

replace_function(
    "scripts/pipeline.py",
    "_build_scrape_run",
    "_parse_row_date",
    '''def _build_scrape_run(
    etf_code: str,
    scrape_date: date,
    data_date: Optional[date],
    started_at: datetime,
    finished_at: datetime,
    result: dict,
    status: str | None = None,
) -> ScrapeRun:
    source_type = result.get("source_type", "")
    final_status = status or ("success" if result["ok"] else "failed")
    usable_result = result["ok"] is True and final_status in {"success", "stale"}

    error = None
    if final_status == "skipped_stale_existing":
        error = "stale_snapshot_already_exists"
    elif final_status == "failed":
        error = result.get("reason")

    return ScrapeRun(
        date=scrape_date,
        data_date=data_date,
        etf_code=etf_code,
        status=final_status,
        primary_source=source_type or "none",
        primary_success=usable_result and source_type == "moneydj_primary",
        moneydj_browser_used=usable_result and source_type == "moneydj_browser",
        official_fallback_used=usable_result and source_type == "official_fallback",
        official_success=usable_result and source_type == "official_fallback",
        rows_extracted=len(result.get("all_rows", [])),
        stock_rows_extracted=len(result.get("stock_rows", [])),
        non_stock_rows_extracted=len(result.get("non_stock_rows", [])),
        total_weight_all_rows=result.get("total_weight_all_rows", 0.0),
        total_weight_stock_rows=result.get("total_weight_stock_rows", 0.0),
        source_url=result.get("source_url") or None,
        error=error,
        started_at=started_at,
        finished_at=finished_at,
    )''',
)

replace_once(
    "scripts/retry_stale_scrapes.py",
    '    """Return active ETFs whose successful scrape rows are older than run_date."""\n',
    '    """Return active ETFs whose run still needs a same-date freshness retry."""\n',
)
replace_once(
    "scripts/retry_stale_scrapes.py",
    '''              AND sr.status = 'success'
              AND sr.data_date IS NOT NULL
              AND sr.data_date < ?
''',
    '''              AND sr.status IN ('stale', 'skipped_stale_existing')
              AND sr.data_date IS NOT NULL
''',
)
replace_once(
    "scripts/retry_stale_scrapes.py",
    '            (run_date, run_date),\n',
    '            (run_date,),\n',
)

replace_function(
    "scripts/db.py",
    "insert_scrape_run",
    "get_last_scrape_date",
    '''def insert_scrape_run(run):
    row = _row_dict(run)
    status_priority = {
        "failed": 0,
        "skipped_stale_existing": 1,
        "stale": 2,
        "success": 3,
    }
    with _connect() as conn:
        existing = conn.execute(
            "SELECT status FROM etf_scrape_runs WHERE date = ? AND etf_code = ?",
            (row["date"], row["etf_code"]),
        ).fetchone()
        if existing and status_priority.get(row["status"], 0) < status_priority.get(existing[0], 0):
            return
        conn.execute("INSERT OR REPLACE INTO etf_scrape_runs (date, data_date, etf_code, status, primary_source, primary_success, moneydj_browser_used, official_fallback_used, official_success, rows_extracted, stock_rows_extracted, non_stock_rows_extracted, total_weight_all_rows, total_weight_stock_rows, source_url, error, started_at, finished_at) VALUES (:date, :data_date, :etf_code, :status, :primary_source, :primary_success, :moneydj_browser_used, :official_fallback_used, :official_success, :rows_extracted, :stock_rows_extracted, :non_stock_rows_extracted, :total_weight_all_rows, :total_weight_stock_rows, :source_url, :error, :started_at, :finished_at)", row)''',
)

retry_tests = Path("tests/test_retry_stale_scrapes.py")
retry_text = retry_tests.read_text(encoding="utf-8")
retry_text = retry_text.replace(
    "def test_get_stale_scrape_runs_selects_only_active_successful_stale_rows():",
    "def test_get_stale_scrape_runs_selects_only_active_retry_eligible_rows():",
)
for code in ("00401A", "00402A", "00405A"):
    retry_text = retry_text.replace(
        f'_insert_scrape_run("{code}", data_date="2026-07-06")',
        f'_insert_scrape_run("{code}", data_date="2026-07-06", status="stale")',
    )
retry_tests.write_text(retry_text, encoding="utf-8")
