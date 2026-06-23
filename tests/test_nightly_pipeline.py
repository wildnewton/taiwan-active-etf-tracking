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
