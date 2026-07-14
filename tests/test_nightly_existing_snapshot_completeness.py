import importlib.util
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "nightly_pipeline.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("nightly_existing_snapshot_test", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_existing_expected_snapshots_do_not_trigger_incomplete_warning(tmp_path, capsys):
    module = _load_module()
    scrape_summary = {
        "date": "2026-07-14",
        "expected_data_date": "2026-07-14",
        "is_trading_day": True,
        "total_etfs": 2,
        "moneydj_success": 0,
        "official_success": 0,
        "skipped_existing_snapshot": 2,
        "failed": 0,
        "failures": [],
        "moneydj_warnings": [],
        "data_freshness": {"fresh": 0, "stale": 0, "unknown": 0},
    }

    with patch.object(module.db, "init_db"), patch.object(
        module, "run_daily_scrape_with_browser", return_value=scrape_summary
    ), patch.object(
        module,
        "detect_holding_changes",
        return_value={"date": "2026-07-14", "skipped_etfs": []},
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

    output = capsys.readouterr().out
    assert "\u8cc7\u6599\u4e0d\u5b8c\u6574" not in output
