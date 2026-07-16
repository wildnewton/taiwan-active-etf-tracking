from pathlib import Path
from unittest.mock import patch

import pytest

import nightly_pipeline
import retry_stale_scrapes


TARGET_DATE = "2026-07-16"
RUN_DATE = "2026-07-16"
OLD_DATE = "2026-07-15"


def _scrape_summary(**overrides):
    summary = {
        "date": RUN_DATE,
        "expected_data_date": TARGET_DATE,
        "total_etfs": 1,
        "preexisting_success": 0,
        "moneydj_success": 1,
        "official_success": 0,
        "failed": 0,
        "failures": [],
        "moneydj_warnings": [],
        "data_freshness": {"fresh": 1, "stale": 0, "unknown": 0},
        "stale_etfs": [],
        "unknown_date_etfs": [],
        "data_date_min": TARGET_DATE,
        "data_date_max": TARGET_DATE,
    }
    summary.update(overrides)
    return summary


def _run_nightly(tmp_path, scrape_summary, *, latest_valid_date=TARGET_DATE, change_summary=None):
    change_summary = change_summary or {
        "ok": True,
        "date": TARGET_DATE,
        "previous_date": OLD_DATE,
        "rows": 1,
        "skipped_etfs": [],
    }
    with patch.object(nightly_pipeline.db, "init_db"), \
         patch.object(nightly_pipeline, "run_daily_scrape_with_browser", return_value=scrape_summary), \
         patch.object(nightly_pipeline, "get_latest_valid_date", return_value=latest_valid_date) as latest, \
         patch.object(nightly_pipeline, "detect_holding_changes", return_value=change_summary) as changes, \
         patch.object(nightly_pipeline, "generate_manager_intent_rollups", return_value={}) as intent, \
         patch.object(nightly_pipeline, "generate_manager_signals", return_value={}) as signals, \
         patch.object(nightly_pipeline, "generate_signal_report", return_value="report") as report, \
         patch.object(nightly_pipeline, "generate_traction_report", return_value="traction") as traction:
        result = nightly_pipeline.run_nightly_pipeline(
            str(tmp_path / "active.sqlite"),
            str(tmp_path / "reports"),
            skip_discovery=True,
        )
    return result, latest, changes, intent, signals, report, traction


def test_nightly_passes_one_validated_target_date_to_every_downstream_stage(tmp_path):
    result, latest, changes, intent, signals, report, traction = _run_nightly(
        tmp_path,
        _scrape_summary(),
    )

    latest.assert_called_once_with()
    changes.assert_called_once_with(current_date=TARGET_DATE)
    intent.assert_called_once_with(TARGET_DATE)
    signals.assert_called_once_with(TARGET_DATE)
    report.assert_called_once_with(TARGET_DATE, quality_run_date=RUN_DATE)
    traction.assert_called_once_with(
        db_path=str(tmp_path / "active.sqlite"),
        window_days=10,
        latest_date=TARGET_DATE,
    )
    assert result["change_summary"]["date"] == TARGET_DATE


def test_nightly_fails_before_change_detection_when_persisted_target_is_missing(tmp_path):
    report_dir = tmp_path / "reports"
    with pytest.raises(RuntimeError, match="persisted holdings.*2026-07-16.*2026-07-15"):
        _run_nightly(
            tmp_path,
            _scrape_summary(),
            latest_valid_date=OLD_DATE,
        )

    assert not list(report_dir.glob("*.txt"))


def test_nightly_rejects_mixed_scrape_dates_before_change_detection(tmp_path):
    with pytest.raises(RuntimeError, match="scrape data date range"):
        _run_nightly(
            tmp_path,
            _scrape_summary(data_date_min=OLD_DATE, data_date_max=TARGET_DATE),
        )


def test_nightly_stops_before_derived_layers_when_change_detection_fails(tmp_path):
    report_dir = tmp_path / "reports"
    with pytest.raises(RuntimeError, match="holding change detection failed"):
        _run_nightly(
            tmp_path,
            _scrape_summary(),
            change_summary={
                "ok": False,
                "date": TARGET_DATE,
                "previous_date": None,
                "rows": 0,
                "reason": "no previous holdings date",
                "skipped_etfs": [],
            },
        )

    assert not list(report_dir.glob("*.txt"))


def _retry_with_improvement(run_date, change_summary):
    with patch.object(retry_stale_scrapes.db, "init_db"), \
         patch.object(
             retry_stale_scrapes,
             "get_stale_scrape_runs",
             side_effect=[
                 [{"etf_code": "00401A", "data_date": OLD_DATE}],
                 [],
             ],
         ), \
         patch.object(retry_stale_scrapes, "run_selected_scrape_with_browser", return_value={}), \
         patch.object(retry_stale_scrapes, "detect_holding_changes", return_value=change_summary) as changes, \
         patch.object(retry_stale_scrapes, "generate_manager_intent_rollups", return_value={}) as intent, \
         patch.object(retry_stale_scrapes, "generate_manager_signals", return_value={}) as signals, \
         patch.object(retry_stale_scrapes, "_overwrite_reports", return_value={}) as reports:
        result = retry_stale_scrapes.retry_stale_etfs(
            db_path=":memory:",
            run_date=run_date,
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
    reports.assert_called_once_with(":memory:", historical_date, Path("reports"))
    assert result["reports_overwritten"] is True


def test_historical_retry_traction_uses_the_explicit_retry_date(tmp_path):
    historical_date = "2026-07-10"
    with patch.object(
        retry_stale_scrapes,
        "generate_signal_report",
        return_value="signal",
    ), patch.object(
        retry_stale_scrapes,
        "generate_traction_report",
        return_value="traction",
    ) as traction:
        retry_stale_scrapes._overwrite_reports(
            ":memory:",
            historical_date,
            tmp_path,
        )

    traction.assert_called_once_with(
        db_path=":memory:",
        window_days=10,
        latest_date=historical_date,
    )


def test_retry_does_not_rebuild_or_overwrite_when_change_detection_fails():
    historical_date = "2026-07-10"
    with pytest.raises(RuntimeError, match="holding change detection failed"):
        _retry_with_improvement(
            historical_date,
            {
                "ok": False,
                "date": historical_date,
                "previous_date": None,
                "rows": 0,
                "reason": "no previous holdings date",
            },
        )
