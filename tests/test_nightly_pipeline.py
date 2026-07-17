"""Tests for the nightly pipeline runner script."""
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = PROJECT_ROOT / "scripts" / "nightly_pipeline.py"

DISCOVERY = {"inserted": [], "reactivated": [], "updated": [], "retired": [], "active_total": 19}
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
    "stale_etfs": [],
    "unknown_date_etfs": [],
    "data_date_min": "2026-06-26",
    "data_date_max": "2026-06-26",
}
STALE_SCRAPE = {
    "date": "2026-06-26",
    "expected_data_date": "2026-06-26",
    "total_etfs": 19,
    "moneydj_success": 19,
    "official_success": 0,
    "failed": 0,
    "failures": [],
    "moneydj_warnings": [],
    "data_freshness": {"fresh": 5, "stale": 14, "unknown": 0},
    "stale_etfs": [
        {"etf_code": "00401A", "data_date": "2026-06-25", "source_type": "moneydj_browser", "reason": "source_date_before_expected_data_date"},
        {"etf_code": "00404A", "data_date": "2026-06-25", "source_type": "moneydj_browser", "reason": "source_date_before_expected_data_date"},
    ],
    "unknown_date_etfs": [],
    "data_date_min": "2026-06-25",
    "data_date_max": "2026-06-26",
}
PARTIAL_SCRAPE = {
    "date": "2026-06-26",
    "expected_data_date": "2026-06-26",
    "total_etfs": 19,
    "moneydj_success": 13,
    "official_success": 0,
    "failed": 6,
    "failures": [{"etf_code": "00401A", "reason": "timeout"}],
    "moneydj_warnings": [],
    "data_freshness": {"fresh": 13, "stale": 0, "unknown": 0},
    "stale_etfs": [],
    "unknown_date_etfs": [],
    "data_date_min": "2026-06-26",
    "data_date_max": "2026-06-26",
}
NO_SKIP_CHANGES = {
    "ok": True, "date": "2026-06-26", "previous_date": "2026-06-25",
    "rows": 995, "new_positions": 2, "removed_positions": 24,
    "skipped_etfs": [],
}
WITH_SKIP_CHANGES = {
    "ok": True, "date": "2026-06-26", "previous_date": "2026-06-25",
    "rows": 631, "new_positions": 2, "removed_positions": 24,
    "skipped_etfs": ["00401A", "00404A", "00405A", "00984A"],
}
MANAGER_INTENT_SUMMARY = {
    "ok": True,
    "date": "2026-06-26",
    "windows": [5, 10],
    "rows": 42,
}


def test_script_exists():
    assert SCRIPT.is_file(), f"Missing {SCRIPT}"


def _run_main(db_path, report_dir):
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
        with patch("sys.argv", ["nightly_pipeline.py", "--db", db_path, "--report-dir", report_dir]):
            mod.main()


def test_script_calls_all_steps(tmp_path):
    db_path = str(tmp_path / "test.sqlite3")
    report_dir = str(tmp_path / "reports")

    with patch("db.init_db") as mock_init, \
         patch("discover_active_etfs.discover_and_reconcile", return_value=DISCOVERY) as mock_discovery, \
         patch("pipeline.run_daily_scrape_with_browser") as mock_scrape, \
         patch("changes.detect_holding_changes") as mock_changes, \
         patch("manager_intent.generate_manager_intent_rollups", return_value=MANAGER_INTENT_SUMMARY) as mock_intent, \
         patch("signals.generate_manager_signals") as mock_signals, \
         patch("report.generate_signal_report") as mock_report, \
         patch("traction_analysis.generate_traction_report") as mock_traction:

        mock_scrape.return_value = COMPLETE_SCRAPE
        mock_changes.return_value = NO_SKIP_CHANGES
        mock_signals.return_value = {"date": "2026-06-26"}
        mock_report.return_value = "Test report"
        mock_traction.return_value = "Test traction"

        _run_main(db_path, report_dir)

        mock_init.assert_called_once_with(db_path)
        assert mock_init.call_count == 1
        mock_discovery.assert_called_once_with(db_path)
        mock_scrape.assert_called_once_with(db_path)
        mock_changes.assert_called_once_with(current_date="2026-06-26")
        mock_intent.assert_called_once_with("2026-06-26")
        mock_signals.assert_called_once_with("2026-06-26")
        mock_report.assert_called_once_with("2026-06-26", quality_run_date="2026-06-26")
        mock_traction.assert_called_once_with(
            db_path=db_path,
            window_days=10,
            latest_date="2026-06-26",
        )


def test_manager_intent_rollups_run_after_changes_before_signals(tmp_path):
    db_path = str(tmp_path / "test.sqlite3")
    report_dir = str(tmp_path / "reports")
    events = []

    def changes_side_effect(current_date=None):
        assert current_date == "2026-06-26"
        events.append("changes")
        return NO_SKIP_CHANGES

    def intent_side_effect(target_date=None):
        events.append(("manager_intent", target_date))
        return MANAGER_INTENT_SUMMARY

    def signals_side_effect(target_date=None):
        events.append(("signals", target_date))
        return {"date": target_date}

    with patch("db.init_db"), \
         patch("discover_active_etfs.discover_and_reconcile", return_value=DISCOVERY), \
         patch("pipeline.run_daily_scrape_with_browser", return_value=COMPLETE_SCRAPE), \
         patch("changes.detect_holding_changes", side_effect=changes_side_effect), \
         patch("manager_intent.generate_manager_intent_rollups", side_effect=intent_side_effect), \
         patch("signals.generate_manager_signals", side_effect=signals_side_effect), \
         patch("report.generate_signal_report", return_value="Signal report text"), \
         patch("traction_analysis.generate_traction_report", return_value="Traction raw text"):
        _run_main(db_path, report_dir)

    assert events == [
        "changes",
        ("manager_intent", "2026-06-26"),
        ("signals", "2026-06-26"),
    ]


def test_manager_intent_summary_is_printed(capsys, tmp_path):
    with patch("db.init_db"), \
         patch("discover_active_etfs.discover_and_reconcile", return_value=DISCOVERY), \
         patch("pipeline.run_daily_scrape_with_browser", return_value=COMPLETE_SCRAPE), \
         patch("changes.detect_holding_changes", return_value=NO_SKIP_CHANGES), \
         patch("manager_intent.generate_manager_intent_rollups", return_value=MANAGER_INTENT_SUMMARY), \
         patch("signals.generate_manager_signals", return_value={}), \
         patch("report.generate_signal_report", return_value=""), \
         patch("traction_analysis.generate_traction_report", return_value=""):
        _run_main(str(tmp_path / "t.sqlite3"), str(tmp_path / "r"))

    out = capsys.readouterr().out
    assert "Generating manager intent rollups" in out
    assert "Manager intent summary" in out
    assert "42" in out


def test_script_writes_primary_and_archive_report_files(tmp_path):
    db_path = str(tmp_path / "test.sqlite3")
    report_dir = str(tmp_path / "reports")

    with patch("db.init_db"), \
         patch("discover_active_etfs.discover_and_reconcile", return_value=DISCOVERY), \
         patch("pipeline.run_daily_scrape_with_browser", return_value=COMPLETE_SCRAPE), \
         patch("changes.detect_holding_changes", return_value=NO_SKIP_CHANGES), \
         patch("manager_intent.generate_manager_intent_rollups", return_value=MANAGER_INTENT_SUMMARY), \
         patch("signals.generate_manager_signals", return_value={}), \
         patch("report.generate_signal_report", return_value="Signal report text"), \
         patch("traction_analysis.generate_traction_report", return_value="Traction raw text"):

        _run_main(db_path, report_dir)

        reports = list(Path(report_dir).glob("*.txt"))
        names = [r.name for r in reports]
        assert "taiwan_active_etf_signal_report_2026-06-26.txt" in names
        assert "traction_raw_2026-06-26.txt" in names
        archive_signal_files = [
            n for n in names
            if n.startswith("taiwan_active_etf_signal_report_2026") and n != "taiwan_active_etf_signal_report_2026-06-26.txt"
        ]
        archive_traction_files = [
            n for n in names
            if n.startswith("traction_raw_2026") and n != "traction_raw_2026-06-26.txt"
        ]
        assert len(archive_signal_files) == 1, f"Expected 1 archive signal report, got {archive_signal_files}"
        assert len(archive_traction_files) == 1, f"Expected 1 archive traction raw, got {archive_traction_files}"
        assert (Path(report_dir) / "taiwan_active_etf_signal_report_2026-06-26.txt").read_text(encoding="utf-8") == "Signal report text"
        assert (Path(report_dir) / "traction_raw_2026-06-26.txt").read_text(encoding="utf-8") == "Traction raw text"


def test_warns_when_incomplete_scrape(capsys, tmp_path):
    with patch("db.init_db"), \
         patch("discover_active_etfs.discover_and_reconcile", return_value=DISCOVERY), \
         patch("pipeline.run_daily_scrape_with_browser", return_value=PARTIAL_SCRAPE), \
         patch("changes.detect_holding_changes", return_value=NO_SKIP_CHANGES), \
         patch("manager_intent.generate_manager_intent_rollups", return_value=MANAGER_INTENT_SUMMARY), \
         patch("signals.generate_manager_signals", return_value={}), \
         patch("report.generate_signal_report", return_value=""), \
         patch("traction_analysis.generate_traction_report", return_value=""):
        _run_main(str(tmp_path / "t.sqlite3"), str(tmp_path / "r"))

    out = capsys.readouterr().out
    assert "預期" in out and "19" in out and "13" in out, f"Expected completeness warning in:\n{out}"
    assert "00401A" in out, f"Expected failing ETF code in output:\n{out}"


def test_stale_summary_is_diagnostic_when_persisted_target_is_complete(capsys, tmp_path):
    with patch("db.init_db"), \
         patch(
             "db.get_target_snapshot_coverage",
             return_value={
                 "actual_count": 19,
                 "expected_count": 19,
                 "missing_etfs": [],
             },
         ), \
         patch("discover_active_etfs.discover_and_reconcile", return_value=DISCOVERY), \
         patch("pipeline.run_daily_scrape_with_browser", return_value=STALE_SCRAPE), \
         patch("changes.detect_holding_changes", return_value=NO_SKIP_CHANGES) as changes, \
         patch("manager_intent.generate_manager_intent_rollups", return_value=MANAGER_INTENT_SUMMARY) as intent, \
         patch("signals.generate_manager_signals", return_value={}) as signals, \
         patch("report.generate_signal_report", return_value="") as report, \
         patch("traction_analysis.generate_traction_report", return_value="") as traction:
        _run_main(str(tmp_path / "t.sqlite3"), str(tmp_path / "r"))

    out = capsys.readouterr().out
    assert "Data freshness" in out
    assert "fresh 5" in out and "stale 14" in out
    assert "STALE SCRAPE" in out
    assert "00401A" in out and "2026-06-25" in out
    changes.assert_called_once_with(current_date="2026-06-26")
    intent.assert_called_once_with("2026-06-26")
    signals.assert_called_once_with("2026-06-26")
    report.assert_called_once_with("2026-06-26", quality_run_date="2026-06-26")
    traction.assert_called_once()


def test_warns_when_skipped_etfs(capsys, tmp_path):
    with patch("db.init_db"), \
         patch("discover_active_etfs.discover_and_reconcile", return_value=DISCOVERY), \
         patch("pipeline.run_daily_scrape_with_browser", return_value=COMPLETE_SCRAPE), \
         patch("changes.detect_holding_changes", return_value=WITH_SKIP_CHANGES), \
         patch("manager_intent.generate_manager_intent_rollups", return_value=MANAGER_INTENT_SUMMARY), \
         patch("signals.generate_manager_signals", return_value={}), \
         patch("report.generate_signal_report", return_value=""), \
         patch("traction_analysis.generate_traction_report", return_value=""):
        _run_main(str(tmp_path / "t.sqlite3"), str(tmp_path / "r"))

    out = capsys.readouterr().out
    assert "跳過" in out, f"Expected skipped-ETF warning in:\n{out}"
    assert "00401A" in out, f"Expected skipped ETF code:\n{out}"
    assert "00404A" in out, f"Expected skipped ETF code:\n{out}"


def test_no_warning_when_complete(capsys, tmp_path):
    with patch("db.init_db"), \
         patch("discover_active_etfs.discover_and_reconcile", return_value=DISCOVERY), \
         patch("pipeline.run_daily_scrape_with_browser", return_value=COMPLETE_SCRAPE), \
         patch("changes.detect_holding_changes", return_value=NO_SKIP_CHANGES), \
         patch("manager_intent.generate_manager_intent_rollups", return_value=MANAGER_INTENT_SUMMARY), \
         patch("signals.generate_manager_signals", return_value={}), \
         patch("report.generate_signal_report", return_value=""), \
         patch("traction_analysis.generate_traction_report", return_value=""):
        _run_main(str(tmp_path / "t.sqlite3"), str(tmp_path / "r"))

    out = capsys.readouterr().out
    assert "預期" not in out, f"Unexpected completeness warning:\n{out}"
    assert "跳過" not in out, f"Unexpected skip warning:\n{out}"
    assert "PROVISIONAL REPORT" not in out, f"Unexpected stale-data warning:\n{out}"
