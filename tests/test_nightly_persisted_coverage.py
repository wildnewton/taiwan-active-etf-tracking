from unittest.mock import patch

import pytest

import nightly_pipeline


TARGET_DATE = "2026-07-17"
RUN_DATE = "2026-07-18"
MISSING_ETF = "E"


def _scrape_summary(data_freshness):
    return {
        "date": RUN_DATE,
        "expected_data_date": TARGET_DATE,
        "is_trading_day": True,
        "total_etfs": 5,
        "preexisting_success": 0,
        # Scrape telemetry says all five sources returned usable results. Persisted
        # complete-snapshot coverage below intentionally says only four are usable.
        "moneydj_success": 5,
        "official_success": 0,
        "failed": 0,
        "failures": [],
        "moneydj_warnings": [],
        "data_freshness": data_freshness,
        "stale_etfs": [
            {
                "etf_code": MISSING_ETF,
                "data_date": "2026-07-16",
                "source_type": "moneydj_primary",
            }
        ]
        if data_freshness["stale"]
        else [],
        "unknown_date_etfs": [],
        "data_date_min": "2026-07-16" if data_freshness["stale"] else TARGET_DATE,
        "data_date_max": TARGET_DATE,
    }


def _run_nightly(tmp_path, scrape_summary):
    coverage = {
        "date": TARGET_DATE,
        "expected_etf_codes": ["A", "B", "C", "D", MISSING_ETF],
        "actual_etf_codes": ["A", "B", "C", "D"],
        "missing_etfs": [MISSING_ETF],
        "latest_available_dates": {MISSING_ETF: "2026-07-16"},
        "expected_count": 5,
        "actual_count": 4,
    }
    with patch.object(nightly_pipeline.db, "init_db"), patch.object(
        nightly_pipeline,
        "run_daily_scrape_with_browser",
        return_value=scrape_summary,
    ), patch.object(
        nightly_pipeline.db,
        "get_target_snapshot_coverage",
        return_value=coverage,
    ) as coverage_query, patch.object(
        nightly_pipeline,
        "get_latest_valid_date",
        return_value=TARGET_DATE,
    ), patch.object(
        nightly_pipeline,
        "detect_holding_changes",
        return_value={
            "ok": True,
            "date": TARGET_DATE,
            "previous_date": "2026-07-16",
            "rows": 1,
            "skipped_etfs": [],
        },
    ), patch.object(
        nightly_pipeline,
        "generate_manager_intent_rollups",
        return_value={},
    ), patch.object(
        nightly_pipeline,
        "generate_manager_signals",
        return_value={},
    ), patch.object(
        nightly_pipeline,
        "generate_signal_report",
        return_value="report",
    ), patch.object(
        nightly_pipeline,
        "generate_traction_report",
        return_value="traction",
    ):
        nightly_pipeline.run_nightly_pipeline(
            str(tmp_path / "active.sqlite"),
            str(tmp_path / "reports"),
            skip_discovery=True,
        )
    return coverage_query


@pytest.mark.parametrize(
    "data_freshness",
    [
        {"fresh": 5, "stale": 0, "unknown": 0},
        {"fresh": 4, "stale": 1, "unknown": 0},
    ],
    ids=["date-matching-incomplete-snapshot", "stale-scrape-result"],
)
def test_nightly_completeness_uses_persisted_coverage_not_scrape_telemetry(
    tmp_path,
    capsys,
    data_freshness,
):
    coverage_query = _run_nightly(tmp_path, _scrape_summary(data_freshness))

    coverage_query.assert_called_once_with(TARGET_DATE)
    output = capsys.readouterr().out
    assert "⚠️ 資料不完整: 預期 5 檔 ETF，實際可用 4 檔" in output
    assert "實際可用 5 檔" not in output
    assert f"缺少目標日持倉: {MISSING_ETF}" in output
