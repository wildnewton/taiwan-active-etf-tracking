import importlib.util
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "nightly_pipeline.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("nightly_existing_snapshot_test", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_with_summary(module, tmp_path, scrape_summary):
    target_date = scrape_summary["expected_data_date"]
    with patch.object(module.db, "init_db"), patch.object(
        module.db,
        "get_target_snapshot_coverage",
        return_value={
            "actual_count": scrape_summary["data_freshness"]["fresh"],
            "expected_count": scrape_summary["total_etfs"],
            "missing_etfs": [],
        },
    ), patch.object(
        module, "run_daily_scrape_with_browser", return_value=scrape_summary
    ), patch.object(
        module, "get_latest_valid_date", return_value=target_date
    ), patch.object(
        module,
        "detect_holding_changes",
        return_value={
            "ok": True,
            "date": target_date,
            "previous_date": "2026-07-13",
            "rows": 1,
            "skipped_etfs": [],
        },
    ), patch.object(
        module, "generate_manager_intent_rollups", return_value={}
    ), patch.object(
        module, "generate_manager_signals", return_value={}
    ), patch.object(
        module, "generate_signal_report", return_value="report"
    ), patch.object(
        module, "generate_traction_report", return_value="traction"
    ):
        module.run_nightly_pipeline(
            str(tmp_path / "active.sqlite"),
            str(tmp_path / "reports"),
            skip_discovery=True,
        )


def test_preexisting_successes_do_not_trigger_incomplete_warning(tmp_path, capsys):
    module = _load_module()
    scrape_summary = {
        "date": "2026-07-14",
        "expected_data_date": "2026-07-14",
        "is_trading_day": True,
        "total_etfs": 2,
        "preexisting_success": 2,
        "moneydj_success": 0,
        "official_success": 0,
        "failed": 0,
        "failures": [],
        "moneydj_warnings": [],
        "data_freshness": {"fresh": 2, "stale": 0, "unknown": 0},
        "data_date_min": "2026-07-14",
        "data_date_max": "2026-07-14",
    }

    _run_with_summary(module, tmp_path, scrape_summary)

    output = capsys.readouterr().out
    assert "資料不完整" not in output


def test_mixed_preexisting_and_new_successes_are_complete(tmp_path, capsys):
    module = _load_module()
    scrape_summary = {
        "date": "2026-07-14",
        "expected_data_date": "2026-07-14",
        "is_trading_day": True,
        "total_etfs": 3,
        "preexisting_success": 1,
        "moneydj_success": 1,
        "official_success": 1,
        "failed": 0,
        "failures": [],
        "moneydj_warnings": [],
        "data_freshness": {"fresh": 3, "stale": 0, "unknown": 0},
        "data_date_min": "2026-07-14",
        "data_date_max": "2026-07-14",
    }

    _run_with_summary(module, tmp_path, scrape_summary)

    output = capsys.readouterr().out
    assert "資料不完整" not in output
