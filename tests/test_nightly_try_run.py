"""Tests for the disposable full nightly try-run mode."""

import importlib.util
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = PROJECT_ROOT / "scripts" / "nightly_pipeline.py"

COMPLETE_SCRAPE = {
    "date": "2026-07-13",
    "expected_data_date": "2026-07-13",
    "total_etfs": 1,
    "moneydj_success": 1,
    "official_success": 0,
    "failed": 0,
    "failures": [],
    "moneydj_warnings": [],
    "data_freshness": {"fresh": 1, "stale": 0, "unknown": 0},
    "stale_etfs": [],
    "unknown_date_etfs": [],
    "data_date_min": "2026-07-13",
    "data_date_max": "2026-07-13",
}


def _load_module():
    spec = importlib.util.spec_from_file_location("nightly_pipeline_try_run_test", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed_database(path: Path):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE seed_state (value TEXT NOT NULL)")
        conn.execute("INSERT INTO seed_state(value) VALUES ('production')")


def _read_seed(path: Path):
    with sqlite3.connect(path) as conn:
        return conn.execute("SELECT value FROM seed_state").fetchone()[0]


def test_cli_try_run_routes_to_disposable_runner(tmp_path):
    module = _load_module()
    db_path = str(tmp_path / "production.sqlite")
    report_dir = str(tmp_path / "reports")

    with patch.object(module, "run_try_run") as run_try_run, \
         patch("sys.argv", [
             "nightly_pipeline.py",
             "--db", db_path,
             "--report-dir", report_dir,
             "--try-run",
             "--skip-discovery",
             "--strict-discovery",
         ]):
        module.main()

    run_try_run.assert_called_once_with(
        db_path,
        report_dir,
        skip_discovery=True,
        strict_discovery=True,
    )


def test_try_run_mutates_only_disposable_database(tmp_path):
    module = _load_module()
    production_db = tmp_path / "production.sqlite"
    production_reports = tmp_path / "reports"
    _seed_database(production_db)
    before_bytes = production_db.read_bytes()
    observed = {}

    def fake_pipeline(db_path, report_dir, **kwargs):
        disposable_db = Path(db_path)
        disposable_reports = Path(report_dir)
        observed["db"] = disposable_db
        observed["reports"] = disposable_reports
        assert disposable_db != production_db
        assert _read_seed(disposable_db) == "production"
        with sqlite3.connect(disposable_db) as conn:
            conn.execute("UPDATE seed_state SET value = 'try-run'")
        disposable_reports.mkdir(parents=True, exist_ok=True)
        (disposable_reports / "probe.txt").write_text("try-run output", encoding="utf-8")
        return {"ok": True}

    with patch.object(module, "run_nightly_pipeline", side_effect=fake_pipeline):
        result = module.run_try_run(str(production_db), str(production_reports))

    assert result == {"ok": True}
    assert production_db.read_bytes() == before_bytes
    assert _read_seed(production_db) == "production"
    assert not production_reports.exists()
    assert not observed["db"].exists()
    assert not observed["reports"].exists()


def test_try_run_downstream_stages_share_the_same_disposable_state(tmp_path):
    module = _load_module()
    production_db = tmp_path / "production.sqlite"
    production_reports = tmp_path / "reports"
    _seed_database(production_db)
    seen = []

    def discovery(db_path):
        with sqlite3.connect(db_path) as conn:
            conn.execute("CREATE TABLE try_run_probe (value TEXT NOT NULL)")
            conn.execute("INSERT INTO try_run_probe(value) VALUES ('discovery')")
        seen.append("discovery")
        return {"discovery_complete": True, "failed_markets": []}

    def scrape(db_path):
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("SELECT value FROM try_run_probe").fetchone()[0] == "discovery"
            conn.execute("UPDATE try_run_probe SET value = 'scrape'")
        seen.append("scrape")
        return COMPLETE_SCRAPE

    def changes(current_date=None):
        assert current_date == "2026-07-13"
        with module.db._connect() as conn:
            assert conn.execute("SELECT value FROM try_run_probe").fetchone()[0] == "scrape"
            conn.execute("UPDATE try_run_probe SET value = 'changes'")
        seen.append("changes")
        return {
            "ok": True,
            "date": current_date,
            "previous_date": "2026-07-12",
            "rows": 1,
            "skipped_etfs": [],
        }

    def intent(target_date):
        assert target_date == "2026-07-13"
        with module.db._connect() as conn:
            assert conn.execute("SELECT value FROM try_run_probe").fetchone()[0] == "changes"
            conn.execute("UPDATE try_run_probe SET value = 'intent'")
        seen.append("intent")
        return {"ok": True, "date": target_date, "rows": 1}

    def signals(target_date):
        assert target_date == "2026-07-13"
        with module.db._connect() as conn:
            assert conn.execute("SELECT value FROM try_run_probe").fetchone()[0] == "intent"
            conn.execute("UPDATE try_run_probe SET value = 'signals'")
        seen.append("signals")
        return {"date": target_date}

    def report(signal_date, quality_run_date=None):
        assert signal_date == "2026-07-13"
        assert quality_run_date == "2026-07-13"
        with module.db._connect() as conn:
            assert conn.execute("SELECT value FROM try_run_probe").fetchone()[0] == "signals"
        seen.append("report")
        return "try-run signal report"

    def traction(db_path, window_days):
        assert window_days == 10
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("SELECT value FROM try_run_probe").fetchone()[0] == "signals"
        seen.append("traction")
        return "try-run traction report"

    with patch.object(module, "discover_and_reconcile", side_effect=discovery), \
         patch.object(module, "run_daily_scrape_with_browser", side_effect=scrape), \
         patch.object(module, "get_latest_valid_date", return_value="2026-07-13"), \
         patch.object(module, "detect_holding_changes", side_effect=changes), \
         patch.object(module, "generate_manager_intent_rollups", side_effect=intent), \
         patch.object(module, "generate_manager_signals", side_effect=signals), \
         patch.object(module, "generate_signal_report", side_effect=report), \
         patch.object(module, "generate_traction_report", side_effect=traction):
        module.run_try_run(str(production_db), str(production_reports))

    assert seen == ["discovery", "scrape", "changes", "intent", "signals", "report", "traction"]
    with sqlite3.connect(production_db) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert "try_run_probe" not in tables
    assert _read_seed(production_db) == "production"
    assert not production_reports.exists()


def test_try_run_cleans_up_after_failure(tmp_path):
    module = _load_module()
    production_db = tmp_path / "production.sqlite"
    _seed_database(production_db)
    before_bytes = production_db.read_bytes()
    observed = {}

    def failing_pipeline(db_path, report_dir, **kwargs):
        observed["db"] = Path(db_path)
        observed["reports"] = Path(report_dir)
        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE seed_state SET value = 'failed-try-run'")
        Path(report_dir).mkdir(parents=True, exist_ok=True)
        raise RuntimeError("injected failure")

    with patch.object(module, "run_nightly_pipeline", side_effect=failing_pipeline):
        with pytest.raises(RuntimeError, match="injected failure"):
            module.run_try_run(str(production_db), str(tmp_path / "reports"))

    assert production_db.read_bytes() == before_bytes
    assert _read_seed(production_db) == "production"
    assert not observed["db"].exists()
    assert not observed["reports"].exists()


def test_normal_mode_still_uses_requested_database_and_report_directory(tmp_path):
    module = _load_module()
    db_path = str(tmp_path / "production.sqlite")
    report_dir = str(tmp_path / "reports")

    with patch.object(module, "run_nightly_pipeline") as run_pipeline, \
         patch("sys.argv", [
             "nightly_pipeline.py",
             "--db", db_path,
             "--report-dir", report_dir,
             "--skip-discovery",
         ]):
        module.main()

    run_pipeline.assert_called_once_with(
        db_path,
        report_dir,
        skip_discovery=True,
        strict_discovery=False,
    )
