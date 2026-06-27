"""Tests for the nightly pipeline runner script."""
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = PROJECT_ROOT / "scripts" / "nightly_pipeline.py"


def test_script_exists():
    """RED: The nightly pipeline script must exist."""
    assert SCRIPT.is_file(), f"Missing {SCRIPT}"


def test_script_calls_all_steps(tmp_path):
    """RED: The runner must call scrape, changes, signals, and report in order."""
    db_path = str(tmp_path / "test.sqlite3")
    report_dir = str(tmp_path / "reports")

    call_order = []

    with patch("db.init_db") as mock_init, \
         patch("pipeline.run_daily_scrape_with_browser") as mock_scrape, \
         patch("changes.detect_holding_changes") as mock_changes, \
         patch("signals.generate_manager_signals") as mock_signals, \
         patch("report.generate_signal_report") as mock_report:

        mock_scrape.return_value = {"date": "2026-06-23", "total_etfs": 19}
        mock_changes.return_value = {"date": "2026-06-23"}
        mock_signals.return_value = {"date": "2026-06-23"}
        mock_report.return_value = "Test report"

        # Import and run main() directly
        import importlib.util
        spec = importlib.util.spec_from_file_location("nightly_pipeline", str(SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        with patch("sys.argv", ["nightly_pipeline.py", "--db", db_path, "--report-dir", report_dir]):
            mod.main()

        mock_init.assert_called_once_with(db_path)
        mock_scrape.assert_called_once_with(db_path)
        mock_changes.assert_called_once()
        mock_signals.assert_called_once()
        mock_report.assert_called_once()


def test_script_writes_report_file(tmp_path):
    """RED: The runner must write the report to a timestamped file."""
    db_path = str(tmp_path / "test.sqlite3")
    report_dir = str(tmp_path / "reports")

    with patch("db.init_db"), \
         patch("pipeline.run_daily_scrape_with_browser", return_value={}), \
         patch("changes.detect_holding_changes", return_value={}), \
         patch("signals.generate_manager_signals", return_value={}), \
         patch("report.generate_signal_report", return_value="Signal report text"):

        import importlib.util
        spec = importlib.util.spec_from_file_location("nightly_pipeline", str(SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        with patch("sys.argv", ["nightly_pipeline.py", "--db", db_path, "--report-dir", report_dir]):
            mod.main()

        reports = list(Path(report_dir).glob("*.txt"))
        assert len(reports) == 1, f"Expected 1 report file, got {len(reports)}"
        assert "taiwan_active_etf_signal_report_" in reports[0].name
        assert reports[0].read_text(encoding="utf-8") == "Signal report text"


# ── Data completeness tests ──

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


def _run_main(db_path, report_dir):
    """Helper: import and run nightly_pipeline.main()."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("nightly_pipeline", str(SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with patch("sys.argv", ["nightly_pipeline.py", "--db", db_path, "--report-dir", report_dir]):
        mod.main()


def test_warns_when_incomplete_scrape(capsys, tmp_path):
    """RED: Should warn when only N/19 ETFs succeeded."""
    with patch("db.init_db"), \
         patch("pipeline.run_daily_scrape_with_browser", return_value=PARTIAL_SCRAPE), \
         patch("changes.detect_holding_changes", return_value=NO_SKIP_CHANGES), \
         patch("signals.generate_manager_signals", return_value={}), \
         patch("report.generate_signal_report", return_value=""):
        _run_main(str(tmp_path / "t.sqlite3"), str(tmp_path / "r"))

    out = capsys.readouterr().out
    assert "預期" in out and "19" in out and "13" in out, \
        f"Expected completeness warning '預期 19 實得 13' in:\n{out}"
    assert "00401A" in out, f"Expected failing ETF code in output:\n{out}"


def test_warns_when_skipped_etfs(capsys, tmp_path):
    """RED: Should warn when change detection skips ETFs."""
    with patch("db.init_db"), \
         patch("pipeline.run_daily_scrape_with_browser", return_value=COMPLETE_SCRAPE), \
         patch("changes.detect_holding_changes", return_value=WITH_SKIP_CHANGES), \
         patch("signals.generate_manager_signals", return_value={}), \
         patch("report.generate_signal_report", return_value=""):
        _run_main(str(tmp_path / "t.sqlite3"), str(tmp_path / "r"))

    out = capsys.readouterr().out
    assert "跳過" in out, f"Expected skipped-ETF warning in:\n{out}"
    assert "00401A" in out, f"Expected skipped ETF code:\n{out}"
    assert "00404A" in out, f"Expected skipped ETF code:\n{out}"


def test_no_warning_when_complete(capsys, tmp_path):
    """RED: No completeness warning when all 19 ETFs succeed and none skipped."""
    with patch("db.init_db"), \
         patch("pipeline.run_daily_scrape_with_browser", return_value=COMPLETE_SCRAPE), \
         patch("changes.detect_holding_changes", return_value=NO_SKIP_CHANGES), \
         patch("signals.generate_manager_signals", return_value={}), \
         patch("report.generate_signal_report", return_value=""):
        _run_main(str(tmp_path / "t.sqlite3"), str(tmp_path / "r"))

    out = capsys.readouterr().out
    assert "預期" not in out, f"Unexpected completeness warning:\n{out}"
    assert "跳過" not in out, f"Unexpected skip warning:\n{out}"
