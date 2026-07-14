from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def replace_section(text: str, start: str, end: str, replacement: str, label: str) -> str:
    start_index = text.find(start)
    if start_index < 0:
        raise SystemExit(f"{label}: start anchor not found")
    end_index = text.find(end, start_index)
    if end_index < 0:
        raise SystemExit(f"{label}: end anchor not found")
    return text[:start_index] + replacement + text[end_index:]


# DB predicate: successful canonical result plus exact snapshot rows.
db_path = Path("scripts/db.py")
db_text = db_path.read_text(encoding="utf-8")
db_replacement = '''def _snapshot_exists(conn, date_value, etf_code):
    holding = conn.execute(
        "SELECT 1 FROM etf_daily_holdings WHERE date = ? AND etf_code = ? LIMIT 1",
        (date_value, etf_code),
    ).fetchone()
    if holding:
        return True
    non_stock = conn.execute(
        "SELECT 1 FROM etf_daily_non_stock_assets WHERE date = ? AND etf_code = ? LIMIT 1",
        (date_value, etf_code),
    ).fetchone()
    return non_stock is not None


def snapshot_exists(date_value, etf_code):
    """Return whether any snapshot rows exist for one ETF/data date."""
    date_value = _serialize(date_value)
    with _connect() as conn:
        return _snapshot_exists(conn, date_value, etf_code)


def successful_snapshot_exists(date_value, etf_code):
    """Return whether an exact snapshot has a canonical successful scrape record."""
    date_value = _serialize(date_value)
    with _connect() as conn:
        successful_run = conn.execute(
            """
            SELECT 1
            FROM etf_scrape_runs
            WHERE etf_code = ?
              AND status = 'success'
              AND data_date = ?
            LIMIT 1
            """,
            (etf_code, date_value),
        ).fetchone()
        return bool(successful_run and _snapshot_exists(conn, date_value, etf_code))
'''
db_text = replace_section(
    db_text,
    "def snapshot_exists(date_value, etf_code):",
    "\n\ndef _snapshot_key",
    db_replacement,
    "db successful snapshot predicate",
)
db_path.write_text(db_text, encoding="utf-8")


pipeline_path = Path("scripts/pipeline.py")
pipeline_text = pipeline_path.read_text(encoding="utf-8")
pipeline_text = replace_once(
    pipeline_text,
    "from db import init_db, insert_scrape_run, replace_daily_snapshot, snapshot_exists",
    "from db import (\n"
    "    init_db,\n"
    "    insert_scrape_run,\n"
    "    replace_daily_snapshot,\n"
    "    snapshot_exists,\n"
    "    successful_snapshot_exists,\n"
    ")",
    "pipeline db imports",
)

browser_daily = '''async def run_daily_scrape_with_browser_async(
    db_path: str = "data/active_etf_holdings.sqlite",
    page=None,
) -> dict:
    if page is not None:
        return await _run_scrape_async(
            db_path,
            None,
            _browser_scrape_fn(page),
        )

    run_date, expected_data_date, summary, etfs_to_scrape = _prepare_scrape_run(
        db_path,
        None,
    )
    if not etfs_to_scrape:
        _finalize_data_date_range(summary)
        return summary

    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            context = await browser.new_context(locale="zh-TW")
            try:
                browser_page = await context.new_page()
                return await _execute_scrape_async(
                    etfs_to_scrape,
                    _browser_scrape_fn(browser_page),
                    run_date,
                    expected_data_date,
                    summary,
                )
            finally:
                await context.close()
        finally:
            await browser.close()
'''
pipeline_text = replace_section(
    pipeline_text,
    "async def run_daily_scrape_with_browser_async(",
    "\n\nasync def run_selected_scrape_with_browser_async(",
    browser_daily,
    "daily browser entry point",
)

runner_block = '''def _run_scrape_sync(
    db_path: str,
    etfs: list[dict],
    scrape_fn: ScrapeFn,
    already_initialized: bool = False,
    use_trading_calendar: bool = True,
    run_at: datetime | None = None,
    skip_existing_snapshot: bool = True,
) -> dict:
    run_date, expected_data_date, summary, etfs_to_scrape = _prepare_scrape_run(
        db_path,
        etfs,
        already_initialized=already_initialized,
        use_trading_calendar=use_trading_calendar,
        run_at=run_at,
        skip_existing_snapshot=skip_existing_snapshot,
    )
    return _execute_scrape_sync(
        etfs_to_scrape,
        scrape_fn,
        run_date,
        expected_data_date,
        summary,
    )


async def _run_scrape_async(
    db_path: str,
    etfs: list[dict] | None,
    scrape_fn: AsyncScrapeFn,
    run_date=None,
    use_trading_calendar: bool = True,
    skip_existing_snapshot: bool = True,
) -> dict:
    run_date, expected_data_date, summary, etfs_to_scrape = _prepare_scrape_run(
        db_path,
        etfs,
        run_date=run_date,
        use_trading_calendar=use_trading_calendar,
        skip_existing_snapshot=skip_existing_snapshot,
    )
    return await _execute_scrape_async(
        etfs_to_scrape,
        scrape_fn,
        run_date,
        expected_data_date,
        summary,
    )


def _prepare_scrape_run(
    db_path: str,
    etfs: list[dict] | None,
    *,
    already_initialized: bool = False,
    use_trading_calendar: bool = True,
    run_at: datetime | None = None,
    run_date=None,
    skip_existing_snapshot: bool = True,
) -> tuple[date, Optional[date], dict, list[dict]]:
    if not already_initialized:
        init_db(db_path)

    if run_at is not None:
        run_at = _as_taipei_run_at(run_at)
    elif run_date is None:
        run_at = _as_taipei_run_at(_current_run_at())
    else:
        run_date = _coerce_run_date(run_date)
        run_at = datetime.combine(
            run_date,
            DATA_AVAILABILITY_CUTOFF,
            tzinfo=TAIPEI_TIMEZONE,
        )

    run_date = run_at.date()
    if etfs is None:
        etfs = _active_etfs_for_run(run_date)
    etfs = list(etfs)

    expected_data_date = _expected_data_date_for_run(run_at, use_trading_calendar)
    is_trading_day = _is_trading_day_for_run(run_date, use_trading_calendar)
    summary = _new_summary(run_date, len(etfs), expected_data_date, is_trading_day)

    if _should_skip_non_trading_day(is_trading_day, use_trading_calendar):
        _record_non_trading_day_skip(summary, len(etfs))
        return run_date, expected_data_date, summary, []

    if not skip_existing_snapshot:
        return run_date, expected_data_date, summary, etfs

    preexisting, etfs_to_scrape = _partition_preexisting_successes(
        etfs,
        expected_data_date,
    )
    _record_preexisting_success(summary, len(preexisting), expected_data_date)
    return run_date, expected_data_date, summary, etfs_to_scrape


def _partition_preexisting_successes(
    etfs: list[dict],
    expected_data_date: Optional[date],
) -> tuple[list[dict], list[dict]]:
    if expected_data_date is None:
        return [], list(etfs)

    preexisting = []
    missing = []
    for etf in etfs:
        target = (
            preexisting
            if successful_snapshot_exists(expected_data_date, etf["code"])
            else missing
        )
        target.append(etf)
    return preexisting, missing


def _record_preexisting_success(
    summary: dict,
    count: int,
    expected_data_date: Optional[date],
) -> None:
    if count <= 0 or expected_data_date is None:
        return
    summary["preexisting_success"] += count
    summary["data_freshness"]["fresh"] += count
    summary["_known_data_dates"].extend([expected_data_date] * count)


def _execute_scrape_sync(
    etfs: list[dict],
    scrape_fn: ScrapeFn,
    run_date: date,
    expected_data_date: Optional[date],
    summary: dict,
) -> dict:
    freshness_target_date = expected_data_date or run_date
    for etf in etfs:
        etf_code = etf["code"]
        started_at = datetime.now()
        result = scrape_fn(etf_code, freshness_target_date)
        finished_at = datetime.now()
        _record_result(
            summary,
            etf_code,
            run_date,
            expected_data_date,
            started_at,
            finished_at,
            result,
        )

    _finalize_data_date_range(summary)
    return summary


async def _execute_scrape_async(
    etfs: list[dict],
    scrape_fn: AsyncScrapeFn,
    run_date: date,
    expected_data_date: Optional[date],
    summary: dict,
) -> dict:
    freshness_target_date = expected_data_date or run_date
    for etf in etfs:
        etf_code = etf["code"]
        started_at = datetime.now()
        result = await scrape_fn(etf_code, freshness_target_date)
        finished_at = datetime.now()
        _record_result(
            summary,
            etf_code,
            run_date,
            expected_data_date,
            started_at,
            finished_at,
            result,
        )

    _finalize_data_date_range(summary)
    return summary
'''
pipeline_text = replace_section(
    pipeline_text,
    "def _run_scrape_sync(",
    "\n\ndef _current_run_at",
    runner_block,
    "pipeline runner block",
)

pipeline_text = replace_once(
    pipeline_text,
    '        "skipped_existing_snapshot": 0,',
    '        "preexisting_success": 0,',
    "summary aggregate",
)
pipeline_text = replace_once(
    pipeline_text,
    '        "existing_snapshot_etfs": [],\n',
    "",
    "remove skip list",
)
pipeline_text = replace_section(
    pipeline_text,
    "def _should_skip_existing_expected_snapshot(",
    "def _validate_snapshot_dates(",
    "",
    "remove persisted skip helpers",
)
pipeline_text = replace_once(
    pipeline_text,
    '    elif status == "skipped_existing_snapshot":\n'
    '        error = "expected_snapshot_already_exists"\n',
    "",
    "remove skip scrape-run status",
)
pipeline_path.write_text(pipeline_text, encoding="utf-8")


nightly_path = Path("scripts/nightly_pipeline.py")
nightly_text = nightly_path.read_text(encoding="utf-8")
nightly_text = replace_once(
    nightly_text,
    '        successful_etfs + scrape_summary.get("skipped_existing_snapshot", 0)\n',
    '        successful_etfs + scrape_summary.get("preexisting_success", 0)\n',
    "nightly availability aggregate",
)
nightly_path.write_text(nightly_text, encoding="utf-8")


# Existing stale-guard tests must now distinguish the precheck predicate from the
# post-scrape row-existence guard.
stale_path = Path("tests/test_skip_stale_existing_snapshots.py")
stale_text = stale_path.read_text(encoding="utf-8")
stale_text = replace_once(
    stale_text,
    '        patch("pipeline._active_etfs_for_run", return_value=ETFS), \\\n'
    '        patch("pipeline.scrape_holdings", return_value=make_success(row_date="2026/06/22")), \\\n'
    '        patch(\n'
    '            "pipeline.snapshot_exists",\n'
    '            side_effect=lambda data_date, _: data_date == STALE_DATA_DATE,\n'
    '        ) as snapshot_exists, \\\n',
    '        patch("pipeline._active_etfs_for_run", return_value=ETFS), \\\n'
    '        patch("pipeline.scrape_holdings", return_value=make_success(row_date="2026/06/22")), \\\n'
    '        patch("pipeline.successful_snapshot_exists", return_value=False) as successful_snapshot_exists, \\\n'
    '        patch(\n'
    '            "pipeline.snapshot_exists",\n'
    '            side_effect=lambda data_date, _: data_date == STALE_DATA_DATE,\n'
    '        ) as snapshot_exists, \\\n',
    "stale existing precheck setup",
)
stale_text = replace_once(
    stale_text,
    '    assert snapshot_exists.call_args_list == [\n'
    '        call(RUN_DATE, "00980A"),\n'
    '        call(STALE_DATA_DATE, "00980A"),\n'
    '    ]\n',
    '    successful_snapshot_exists.assert_called_once_with(RUN_DATE, "00980A")\n'
    '    snapshot_exists.assert_called_once_with(STALE_DATA_DATE, "00980A")\n',
    "stale existing assertions",
)
stale_text = replace_once(
    stale_text,
    '        patch("pipeline._active_etfs_for_run", return_value=ETFS), \\\n'
    '        patch("pipeline.scrape_holdings", return_value=make_success(row_date="2026/06/22")), \\\n'
    '        patch("pipeline.snapshot_exists", return_value=False) as snapshot_exists, \\\n',
    '        patch("pipeline._active_etfs_for_run", return_value=ETFS), \\\n'
    '        patch("pipeline.scrape_holdings", return_value=make_success(row_date="2026/06/22")), \\\n'
    '        patch("pipeline.successful_snapshot_exists", return_value=False) as successful_snapshot_exists, \\\n'
    '        patch("pipeline.snapshot_exists", return_value=False) as snapshot_exists, \\\n',
    "stale missing precheck setup",
)
stale_text = replace_once(
    stale_text,
    '    assert snapshot_exists.call_args_list == [\n'
    '        call(RUN_DATE, "00980A"),\n'
    '        call(STALE_DATA_DATE, "00980A"),\n'
    '    ]\n',
    '    successful_snapshot_exists.assert_called_once_with(RUN_DATE, "00980A")\n'
    '    snapshot_exists.assert_called_once_with(STALE_DATA_DATE, "00980A")\n',
    "stale missing assertions",
)
stale_text = replace_once(
    stale_text,
    '        patch("pipeline._active_etfs_for_run", return_value=ETFS), \\\n'
    '        patch("pipeline.scrape_holdings", return_value=make_success(row_date="2026/06/23")), \\\n'
    '        patch("pipeline.snapshot_exists", return_value=False) as snapshot_exists, \\\n',
    '        patch("pipeline._active_etfs_for_run", return_value=ETFS), \\\n'
    '        patch("pipeline.scrape_holdings", return_value=make_success(row_date="2026/06/23")), \\\n'
    '        patch("pipeline.successful_snapshot_exists", return_value=False) as successful_snapshot_exists, \\\n'
    '        patch("pipeline.snapshot_exists", return_value=False) as snapshot_exists, \\\n',
    "fresh precheck setup",
)
stale_text = replace_once(
    stale_text,
    '    snapshot_exists.assert_called_once_with(RUN_DATE, "00980A")\n',
    '    successful_snapshot_exists.assert_called_once_with(RUN_DATE, "00980A")\n'
    '    snapshot_exists.assert_not_called()\n',
    "fresh guard assertions",
)
stale_path.write_text(stale_text, encoding="utf-8")


# The older test file encoded the superseded persisted-skip contract. The new
# integration suite covers its useful cases with real SQLite and both browser paths.
old_test_path = Path("tests/test_skip_existing_expected_snapshots.py")
if old_test_path.exists():
    old_test_path.unlink()

print("Applied validated preexisting snapshot implementation")
