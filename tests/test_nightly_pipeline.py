"""Tests for the nightly pipeline runner script."""
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = PROJECT_ROOT / "scripts" / "nightly_pipeline.py"

DISCOVERY = {"inserted": [], "reactivated": [], "updated": [], "retired": [], "active_total": 19}
COMPLETE_SCRAPE = {
    "date": "2026-06-26",
    "data_date": "2026-06-26",
    "total_etfs": 19,
    "moneydj_success": 19,
    "official_success": 0,
    "failed": 0,
    "failures": [],
    "moneydj_warnings": [],
}
PARTIAL_SCRAPE = {
    "date": "2026-06-26",
    "data_date": "2026-06-26",
    "total_etfs": 19,
    "moneydj_success": 13,
    "official_success": 0,
    "failed": 6,
    "failures": [{"etf_code": "00401A", "reason": "timeout"}],
    "moneydj_warnings": [],
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


def test_script_exists():
    assert SCRIPT.is_file(), f"Missing {SCRIPT}"


def _run_main(db_path, report_dir):
    import importlib.util
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
         patch("signals.generate_manager_signals") as mock_signals, \
         patch("report.generate_signal_report") as mock_report, \
         patch("traction_analysis.generate_traction_report") as mock_traction:

        mock_scrape.return_value = COMPLETE_SCRAPE
        mock_changes.return_value = {"date": "2026-06-23", "skipped_etfs": []}
        mock_signals.return_value = {"date": "2026-06-23"}
        mock_report.return_value = "Test report"
        mock_traction.return_value = "Test traction"

        _run_main(db_path, report_dir)

        mock_init.assert_called_once_with(db_path)
        assert mock_init.call_count == 1
        mock_discovery.assert_called_once_with(db_path)
        mock_scrape.assert_called_once_with(db_path)
        mock_changes.assert_called_once()
        mock_signals.assert_called_once()
        mock_report.assert_called_once()
        mock_traction.assert_called_once()


def test_script_writes_report_file(tmp_path):
    db_path = str(tmp_path / "test.sqlite3")
    report_dir = str(tmp_path / "reports")

    with patch("db.init_db"), \
         patch("discover_active_etfs.discover_and_reconcile", return_value=DISCOVERY), \
         patch("pipeline.run_daily_scrape_with_browser", return_value=COMPLETE_SCRAPE), \
         patch("changes.detect_holding_changes", return_value={"skipped_etfs": []}), \
         patch("signals.generate_manager_signals", return_value={}), \
         patch("report.generate_signal_report", return_value="Signal report text"), \
         patch("traction_analysis.generate_traction_report", return_value="Traction raw text"):

        _run_main(db_path, report_dir)

        reports = list(Path(report_dir).glob("*.txt"))
        assert len(reports) == 2, f"Expected 2 report files, got {len(reports)}: {[r.name for r in reports]}"
        names = [r.name for r in reports]
        signal_files = [n for n in names if "taiwan_active_etf_signal_report_" in n]
        traction_files = [n for n in names if "traction_raw_" in n]
        assert len(signal_files) == 1, f"Expected 1 signal report, got {signal_files}"
        assert len(traction_files) == 1, f"Expected 1 traction raw, got {traction_files}"
        signal_path = [r for r in reports if "taiwan_active_etf_signal_report_" in r.name][0]
        assert signal_path.read_text(encoding="utf-8") == "Signal report text"


def test_warns_when_incomplete_scrape(capsys, tmp_path):
    with patch("db.init_db"), \
         patch("discover_active_etfs.discover_and_reconcile", return_value=DISCOVERY), \
         patch("pipeline.run_daily_scrape_with_browser", return_value=PARTIAL_SCRAPE), \
         patch("changes.detect_holding_changes", return_value=NO_SKIP_CHANGES), \
         patch("signals.generate_manager_signals", return_value={}), \
         patch("report.generate_signal_report", return_value=""), \
         patch("traction_analysis.generate_traction_report", return_value=""):
        _run_main(str(tmp_path / "t.sqlite3"), str(tmp_path / "r"))

    out = capsys.readouterr().out
    assert "預期" in out and "19" in out and "13" in out, f"Expected completeness warning in:\n{out}"
    assert "00401A" in out, f"Expected failing ETF code in output:\n{out}"


def test_warns_when_skipped_etfs(capsys, tmp_path):
    with patch("db.init_db"), \
         patch("discover_active_etfs.discover_and_reconcile", return_value=DISCOVERY), \
         patch("pipeline.run_daily_scrape_with_browser", return_value=COMPLETE_SCRAPE), \
         patch("changes.detect_holding_changes", return_value=WITH_SKIP_CHANGES), \
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
         patch("signals.generate_manager_signals", return_value={}), \
         patch("report.generate_signal_report", return_value=""), \
         patch("traction_analysis.generate_traction_report", return_value=""):
        _run_main(str(tmp_path / "t.sqlite3"), str(tmp_path / "r"))

    out = capsys.readouterr().out
    assert "預期" not in out, f"Unexpected completeness warning:\n{out}"
    assert "跳過" not in out, f"Unexpected skip warning:\n{out}"
