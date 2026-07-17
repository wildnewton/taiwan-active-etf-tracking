from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def update(path, old, new, label):
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Two bounded-worker cases used a multiline variant of the old recorder signature.
path = ROOT / "tests/test_bounded_async_scraping.py"
text = path.read_text(encoding="utf-8")
text = text.replace(
    "def record_result(summary, etf_code, run_date, expected_date, started_at, finished_at, result):",
    "def record_result(summary, etf_code, run_date, expected_date, result):",
)
text = text.replace(
    '''def record_result(
        summary,
        etf_code,
        run_date,
        expected_date,
        started_at,
        finished_at,
        result,
    ):
''',
    '''def record_result(summary, etf_code, run_date, expected_date, result):
''',
)
path.write_text(text, encoding="utf-8")


# Tests that mock DB initialization must also supply persisted target coverage.
for test_path in [
    "tests/test_nightly_pipeline.py",
    "tests/test_nightly_discovery_status.py",
]:
    target = ROOT / test_path
    text = target.read_text(encoding="utf-8")
    old = '    with patch("changes.get_latest_valid_date", return_value="2026-06-26"):\n'
    new = '''    with patch("changes.get_latest_valid_date", return_value="2026-06-26"), patch(
        "db.get_target_snapshot_coverage",
        return_value={
            "actual_count": 19,
            "expected_count": 19,
            "missing_etfs": [],
        },
    ):
'''
    if text.count(old) != 1:
        raise RuntimeError(f"{test_path}: run-main coverage marker")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


update(
    "tests/test_nightly_existing_snapshot_completeness.py",
    '''    with patch.object(module.db, "init_db"), patch.object(
        module, "run_daily_scrape_with_browser", return_value=scrape_summary
''',
    '''    with patch.object(module.db, "init_db"), patch.object(
        module.db,
        "get_target_snapshot_coverage",
        return_value={
            "actual_count": scrape_summary["data_freshness"]["fresh"],
            "expected_count": scrape_summary["total_etfs"],
            "missing_etfs": [],
        },
    ), patch.object(
        module, "run_daily_scrape_with_browser", return_value=scrape_summary
''',
    "existing snapshot coverage",
)


update(
    "tests/test_selected_pipeline_retry.py",
    '    assert scraper.await_args.args[2] == date(2026, 7, 6)\n',
    '    assert scraper.await_args.kwargs["target_date"] == date(2026, 7, 6)\n',
    "selected retry target assertion",
)


update(
    "tests/test_weight_validation_warnings.py",
    '''    with patch("pipeline.replace_daily_snapshot", return_value={"inserted": True}), patch(
        "pipeline.insert_scrape_run"
    ), patch(
        "scrapers.moneydj.scrape_moneydj", diagnostic_scrape
    ):
''',
    '''    with patch("pipeline.replace_daily_snapshot", return_value={"inserted": True}), patch(
        "scrapers.moneydj.scrape_moneydj", diagnostic_scrape
    ):
''',
    "weight warning removed writer",
)

print("Applied remaining holdings-source test fixes")
