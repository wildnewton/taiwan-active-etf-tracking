from unittest.mock import patch

import nightly_pipeline


NON_TRADING_SUMMARY = {
    "date": "2026-06-27",
    "expected_data_date": "2026-06-26",
    "is_trading_day": False,
    "skip_reason": "tw_stock_market_closed",
    "total_etfs": 2,
    "moneydj_success": 0,
    "official_success": 0,
    "failed": 0,
    "skipped_non_trading_day": 2,
    "skipped_stale_existing": 0,
    "total_stock_rows": 0,
    "total_non_stock_rows": 0,
    "failures": [],
    "moneydj_warnings": [],
    "row_count_warnings": [],
    "data_freshness": {"fresh": 0, "stale": 0, "unknown": 0},
    "stale_etfs": [],
    "stale_existing_etfs": [],
    "unknown_date_etfs": [],
    "data_date_min": None,
    "data_date_max": None,
}


def test_nightly_pipeline_stops_after_non_trading_day_scrape_skip(capsys):
    with patch("sys.argv", ["nightly_pipeline.py", "--skip-discovery"]), \
        patch("nightly_pipeline.db.init_db"), \
        patch("nightly_pipeline.run_daily_scrape_with_browser", return_value=NON_TRADING_SUMMARY), \
        patch("nightly_pipeline.detect_holding_changes") as detect_holding_changes, \
        patch("nightly_pipeline.generate_manager_intent_rollups") as generate_manager_intent_rollups, \
        patch("nightly_pipeline.generate_manager_signals") as generate_manager_signals, \
        patch("nightly_pipeline.generate_signal_report") as generate_signal_report, \
        patch("nightly_pipeline.generate_traction_report") as generate_traction_report:
        nightly_pipeline.main()

    detect_holding_changes.assert_not_called()
    generate_manager_intent_rollups.assert_not_called()
    generate_manager_signals.assert_not_called()
    generate_signal_report.assert_not_called()
    generate_traction_report.assert_not_called()

    output = capsys.readouterr().out
    assert "TW stock market closed" in output
    assert "Skipping downstream steps" in output
