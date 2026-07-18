from unittest.mock import patch

import pytest

import nightly_pipeline


RUN_DATE = "2026-07-18"
TARGET_DATE = "2026-07-17"
PREVIOUS_DATE = "2026-07-16"


def _scrape_summary(
    *,
    preexisting_success,
    moneydj_success=0,
    official_success=0,
    failed=0,
    fresh=None,
    stale=0,
):
    if fresh is None:
        fresh = preexisting_success + moneydj_success + official_success
    return {
        "date": RUN_DATE,
        "expected_data_date": TARGET_DATE,
        "is_trading_day": False,
        "skip_reason": None,
        "total_etfs": 2,
        "preexisting_success": preexisting_success,
        "moneydj_success": moneydj_success,
        "official_success": official_success,
        "failed": failed,
        "skipped_non_trading_day": 0,
        "skipped_stale_existing": 0,
        "total_stock_rows": 0,
        "total_non_stock_rows": 0,
        "failures": [],
        "moneydj_warnings": [],
        "row_count_warnings": [],
        "weight_warnings": [],
        "data_freshness": {"fresh": fresh, "stale": stale, "unknown": 0},
        "stale_etfs": [],
        "stale_existing_etfs": [],
        "unknown_date_etfs": [],
        "data_date_min": TARGET_DATE if fresh else None,
        "data_date_max": TARGET_DATE if fresh else None,
    }


def _coverage(actual_count, missing_etfs):
    return {
        "date": TARGET_DATE,
        "expected_etf_codes": ["A", "B"],
        "actual_etf_codes": ["A", "B"][:actual_count],
        "missing_etfs": missing_etfs,
        "latest_available_dates": {
            code: PREVIOUS_DATE for code in missing_etfs
        },
        "expected_count": 2,
        "actual_count": actual_count,
    }


def _run_with_downstream_patches(tmp_path, scrape_summary, coverage):
    patches = {
        "detect": patch.object(
            nightly_pipeline,
            "detect_holding_changes",
            return_value={
                "ok": True,
                "date": TARGET_DATE,
                "previous_date": PREVIOUS_DATE,
                "rows": 1,
                "skipped_etfs": [],
            },
        ),
        "rollups": patch.object(
            nightly_pipeline,
            "generate_manager_intent_rollups",
            return_value={},
        ),
        "signals": patch.object(
            nightly_pipeline,
            "generate_manager_signals",
            return_value={},
        ),
        "report": patch.object(
            nightly_pipeline,
            "generate_signal_report",
            return_value="report",
        ),
        "traction": patch.object(
            nightly_pipeline,
            "generate_traction_report",
            return_value="traction",
        ),
    }
    with patch.object(nightly_pipeline.db, "init_db"), patch.object(
        nightly_pipeline,
        "run_daily_scrape_with_browser",
        return_value=scrape_summary,
    ), patch.object(
        nightly_pipeline.db,
        "get_target_snapshot_coverage",
        return_value=coverage,
    ), patch.object(
        nightly_pipeline,
        "get_latest_valid_date",
        return_value=TARGET_DATE,
    ), patches["detect"] as detect, patches["rollups"] as rollups, patches[
        "signals"
    ] as signals, patches["report"] as report, patches["traction"] as traction:
        result = nightly_pipeline.run_nightly_pipeline(
            str(tmp_path / "active.sqlite"),
            str(tmp_path / "reports"),
            skip_discovery=True,
        )
    return result, detect, rollups, signals, report, traction


def test_non_trading_day_with_complete_preexisting_target_is_clean_noop(tmp_path):
    summary = _scrape_summary(preexisting_success=2)
    result, detect, rollups, signals, report, traction = _run_with_downstream_patches(
        tmp_path,
        summary,
        _coverage(2, []),
    )

    assert result == {
        "scrape_summary": summary,
        "skipped_downstream": True,
        "downstream_skip_reason": "target_snapshot_already_complete",
    }
    detect.assert_not_called()
    rollups.assert_not_called()
    signals.assert_not_called()
    report.assert_not_called()
    traction.assert_not_called()


@pytest.mark.parametrize(
    "summary",
    [
        _scrape_summary(preexisting_success=1, failed=1, fresh=1),
        _scrape_summary(
            preexisting_success=1,
            moneydj_success=1,
            fresh=1,
            stale=1,
        ),
    ],
    ids=["failed-source", "stale-source-success"],
)
def test_non_trading_day_gap_without_new_complete_snapshot_fails(
    tmp_path,
    summary,
):
    with patch.object(nightly_pipeline.db, "init_db"), patch.object(
        nightly_pipeline,
        "run_daily_scrape_with_browser",
        return_value=summary,
    ), patch.object(
        nightly_pipeline.db,
        "get_target_snapshot_coverage",
        return_value=_coverage(1, ["B"]),
    ), patch.object(
        nightly_pipeline,
        "detect_holding_changes",
    ) as detect:
        with pytest.raises(
            RuntimeError,
            match="non-trading-day recovery produced no complete target snapshots",
        ):
            nightly_pipeline.run_nightly_pipeline(
                str(tmp_path / "active.sqlite"),
                str(tmp_path / "reports"),
                skip_discovery=True,
            )

    detect.assert_not_called()


def test_non_trading_day_recovery_runs_downstream_for_target_date(tmp_path):
    summary = _scrape_summary(
        preexisting_success=1,
        moneydj_success=1,
        fresh=2,
    )
    result, detect, rollups, signals, report, traction = _run_with_downstream_patches(
        tmp_path,
        summary,
        _coverage(2, []),
    )

    assert result["scrape_summary"] == summary
    assert result["target_data_date"] == TARGET_DATE
    detect.assert_called_once_with(current_date=TARGET_DATE)
    rollups.assert_called_once_with(TARGET_DATE)
    signals.assert_called_once_with(TARGET_DATE)
    report.assert_called_once_with(TARGET_DATE, quality_run_date=RUN_DATE)
    traction.assert_called_once()
