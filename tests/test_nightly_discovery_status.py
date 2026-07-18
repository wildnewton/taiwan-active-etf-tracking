from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = PROJECT_ROOT / "scripts" / "nightly_pipeline.py"

INCOMPLETE_DISCOVERY = {
    "discovery_complete": False,
    "failed_markets": [{"market": "TPEx", "reason": "timeout"}],
    "completed_markets": ["TWSE"],
    "inserted": [],
    "reactivated": [],
    "updated": [],
    "retired": [],
    "retirement_candidates": [],
    "active_total": 19,
}
COMPLETE_DISCOVERY_WITH_CANDIDATE = {
    "discovery_complete": True,
    "failed_markets": [],
    "completed_markets": ["TWSE", "TPEx"],
    "inserted": [],
    "reactivated": [],
    "updated": [],
    "retired": [],
    "retirement_candidates": ["00980A"],
    "active_total": 19,
}
COMPLETE_SCRAPE = {
    "date": "2026-06-26",
    "expected_data_date": "2026-06-26",
    "total_etfs": 19,
    "moneydj_success": 19,
    "official_success": 0,
    "failed": 0,
    "failures": [],
    "moneydj_warnings": [],
    "data_freshness": {"fresh": 19, "stale": 0, "unknown": 0},
    "data_date_min": "2026-06-26",
    "data_date_max": "2026-06-26",
}
NO_SKIP_CHANGES = {
    "ok": True,
    "date": "2026-06-26",
    "previous_date": "2026-06-25",
    "rows": 0,
    "skipped_etfs": [],
}
MANAGER_INTENT_SUMMARY = {
    "ok": True,
    "date": "2026-06-26",
    "windows": [5, 10],
    "rows": 0,
}


def _run_main(db_path, report_dir, extra_args=None):
    import importlib.util

    with patch("changes.get_latest_valid_date", return_value="2026-06-26"), patch(
        "db.get_target_snapshot_coverage",
        return_value={
            "actual_count": 19,
            "expected_count": 19,
            "missing_etfs": [],
        },
    ):
        spec = importlib.util.spec_from_file_location("nightly_pipeline", str(SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        argv = ["nightly_pipeline.py", "--db", db_path, "--report-dir", report_dir]
        if extra_args:
            argv.extend(extra_args)
        with patch("sys.argv", argv):
            return mod.main()


def _patched_nightly(discovery_summary):
    return (
        patch("db.init_db"),
        patch(
            "discover_active_etfs.discover_and_reconcile",
            return_value=discovery_summary,
        ),
        patch(
            "pipeline.run_daily_scrape_with_browser",
            return_value=COMPLETE_SCRAPE,
        ),
        patch("changes.detect_holding_changes", return_value=NO_SKIP_CHANGES),
        patch(
            "manager_intent.generate_manager_intent_rollups",
            return_value=MANAGER_INTENT_SUMMARY,
        ),
        patch("signals.generate_manager_signals", return_value={}),
        patch("report.generate_signal_report", return_value=""),
        patch("traction_analysis.generate_traction_report", return_value=""),
    )


def test_nightly_warns_and_continues_on_incomplete_discovery(capsys, tmp_path):
    patches = _patched_nightly(INCOMPLETE_DISCOVERY)
    with (
        patches[0],
        patches[1],
        patches[2] as scrape,
        patches[3],
        patches[4] as intent,
        patches[5],
        patches[6],
        patches[7],
    ):
        _run_main(str(tmp_path / "t.sqlite3"), str(tmp_path / "r"))

    out = capsys.readouterr().out
    assert "ETF universe discovery incomplete" in out
    assert "TPEx" in out
    scrape.assert_called_once()
    intent.assert_called_once_with("2026-06-26")


def test_nightly_surfaces_retirement_candidates_and_continues(capsys, tmp_path):
    patches = _patched_nightly(COMPLETE_DISCOVERY_WITH_CANDIDATE)
    with (
        patches[0],
        patches[1],
        patches[2] as scrape,
        patches[3],
        patches[4] as intent,
        patches[5],
        patches[6],
        patches[7],
    ):
        _run_main(str(tmp_path / "t.sqlite3"), str(tmp_path / "r"))

    out = capsys.readouterr().out
    assert "retirement_candidates" in out
    assert "00980A" in out
    scrape.assert_called_once()
    intent.assert_called_once_with("2026-06-26")


def test_nightly_strict_mode_stops_on_incomplete_discovery(tmp_path):
    with patch("db.init_db"), patch(
        "discover_active_etfs.discover_and_reconcile",
        return_value=INCOMPLETE_DISCOVERY,
    ), patch("pipeline.run_daily_scrape_with_browser") as scrape:
        stopped = False
        try:
            _run_main(
                str(tmp_path / "t.sqlite3"),
                str(tmp_path / "r"),
                ["--strict-discovery"],
            )
        except RuntimeError as exc:
            stopped = "ETF universe discovery incomplete" in str(exc)

    assert stopped is True
    scrape.assert_not_called()
