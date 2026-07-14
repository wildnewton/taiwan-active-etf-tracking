from pathlib import Path

pipeline_path = Path("scripts/pipeline.py")
pipeline_text = pipeline_path.read_text(encoding="utf-8")

selected_old = (
    "                    run_date=run_date,\n"
    "                    use_trading_calendar=False,\n"
    "                )"
)
selected_new = (
    "                    run_date=run_date,\n"
    "                    use_trading_calendar=False,\n"
    "                    skip_existing_snapshot=False,\n"
    "                )"
)
if pipeline_text.count(selected_old) != 1:
    raise RuntimeError(f"expected one remaining selected call, found {pipeline_text.count(selected_old)}")
pipeline_text = pipeline_text.replace(selected_old, selected_new, 1)
pipeline_text = pipeline_text.replace(
    '    summary["skip_reason"] = "tw_stock_market_closed"\n\n\n\n',
    '    summary["skip_reason"] = "tw_stock_market_closed"\n\n',
    1,
)
pipeline_text = pipeline_text.replace(
    '    )\ndef _validate_snapshot_dates',
    '    )\n\n\ndef _validate_snapshot_dates',
    1,
)
pipeline_path.write_text(pipeline_text, encoding="utf-8")

test_path = Path("tests/test_skip_stale_existing_snapshots.py")
test_text = test_path.read_text(encoding="utf-8")
anchor = '        patch("pipeline._active_etfs_for_run", return_value=ETFS), \\\n'
replacement = (
    '        patch("pipeline.latest_tw_trading_day_on_or_before", return_value=RUN_DATE), \\\n'
    '        patch("pipeline.is_tw_trading_day", return_value=True), \\\n'
    + anchor
)
if test_text.count(anchor) != 3:
    raise RuntimeError(f"expected three stale-test anchors, found {test_text.count(anchor)}")
test_text = test_text.replace(anchor, replacement)
test_path.write_text(test_text, encoding="utf-8")
