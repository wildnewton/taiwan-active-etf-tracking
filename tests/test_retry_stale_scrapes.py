from unittest.mock import patch

import db
from retry_stale_scrapes import get_stale_scrape_runs, retry_stale_etfs, stale_count


def _init_db(path):
    db_path = str(path / "test.sqlite3")
    db.init_db(db_path)
    return db_path


def _seed_etf(code, retired=0):
    with db._connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO etf_universe (
                code, name, retired, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (code, code, retired, "2026-07-07T00:00:00", "2026-07-07T00:00:00"),
        )


def _insert_scrape_run(code, *, run_date="2026-07-07", data_date="2026-07-06", status="stale"):
    valid_snapshot = status in {"success", "stale", "skipped_stale_existing"}
    with db._connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO etf_scrape_runs (
                date, data_date, etf_code, status, primary_source, primary_success,
                moneydj_browser_used, official_fallback_used, official_success,
                rows_extracted, stock_rows_extracted, non_stock_rows_extracted,
                total_weight_all_rows, total_weight_stock_rows, source_url, error,
                started_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_date, data_date, code, status, "moneydj_primary",
                1 if status == "success" else 0, 0, 0, 0,
                1 if valid_snapshot else 0,
                1 if valid_snapshot else 0,
                0,
                10.0 if valid_snapshot else 0.0,
                10.0 if valid_snapshot else 0.0,
                "https://example.test" if valid_snapshot else "",
                None if valid_snapshot else "timeout",
                "2026-07-07T21:00:00",
                "2026-07-07T21:00:01",
            ),
        )


def _seed_retry_rows():
    db.init_db(":memory:")
    _seed_etf("00401A")
    _seed_etf("00402A")
    _seed_etf("00403A")
    _seed_etf("00404A")
    _seed_etf("00405A", retired=1)
    _insert_scrape_run("00401A", data_date="2026-07-06", status="stale")
    _insert_scrape_run("00402A", data_date="2026-07-07", status="success")
    _insert_scrape_run("00403A", data_date="2026-07-06", status="failed")
    _insert_scrape_run("00404A", data_date=None)
    _insert_scrape_run("00405A", data_date="2026-07-06", status="stale")


def test_get_stale_scrape_runs_selects_only_active_retry_eligible_rows():
    _seed_retry_rows()

    rows = get_stale_scrape_runs("2026-07-07")

    assert rows == [{"etf_code": "00401A", "data_date": "2026-07-06"}]
    assert stale_count("2026-07-07") == 1


def test_retry_stale_etfs_retries_only_stale_and_overwrites_reports_when_improved(tmp_path):
    db_path = _init_db(tmp_path)
    _seed_etf("00401A")
    _seed_etf("00402A")
    _insert_scrape_run("00401A", data_date="2026-07-06", status="stale")
    _insert_scrape_run("00402A", data_date="2026-07-06", status="stale")

    retry_summary = {
        "date": "2026-07-07",
        "total_etfs": 2,
        "data_freshness": {"fresh": 1, "stale": 1, "unknown": 0},
        "stale_etfs": [{"etf_code": "00402A", "data_date": "2026-07-06"}],
    }

    with patch("retry_stale_scrapes.run_selected_scrape_with_browser", return_value=retry_summary) as scrape, \
        patch("retry_stale_scrapes.detect_holding_changes", return_value={"date": "2026-07-07"}) as changes, \
        patch("retry_stale_scrapes.generate_manager_intent_rollups", return_value={"rows": 3}) as intent, \
        patch("retry_stale_scrapes.generate_manager_signals", return_value={"rows": 4}) as signals, \
        patch("retry_stale_scrapes.generate_signal_report", return_value="updated signal report") as signal_report, \
        patch("retry_stale_scrapes.generate_traction_report", return_value="updated traction report") as traction_report:
        summary = retry_stale_etfs(db_path=db_path, run_date="2026-07-07", report_dir=tmp_path)

    scrape.assert_called_once_with(db_path, ["00401A", "00402A"], run_date="2026-07-07")
    changes.assert_called_once_with(current_date="2026-07-07")
    intent.assert_called_once_with("2026-07-07")
    signals.assert_called_once()
    signal_report.assert_called_once_with("2026-07-07")
    traction_report.assert_called_once_with(db_path=db_path, window_days=10)

    assert summary["stale_before"] == 2
    assert summary["stale_after"] == 1
    assert summary["improved"] is True
    assert summary["reports_overwritten"] is True
    assert (tmp_path / "taiwan_active_etf_signal_report_2026-07-07.txt").read_text(encoding="utf-8") == "updated signal report"
    assert (tmp_path / "traction_raw_2026-07-07.txt").read_text(encoding="utf-8") == "updated traction report"


def test_retry_stale_etfs_failed_retry_does_not_count_as_improvement(tmp_path):
    db_path = _init_db(tmp_path)
    _seed_etf("00401A")
    _insert_scrape_run("00401A", data_date="2026-07-06", status="stale")

    retry_summary = {
        "date": "2026-07-07",
        "total_etfs": 1,
        "failed": 1,
        "data_freshness": {"fresh": 0, "stale": 0, "unknown": 0},
        "failures": [{"etf_code": "00401A", "reason": "timeout"}],
    }

    with patch("retry_stale_scrapes.run_selected_scrape_with_browser", return_value=retry_summary), \
        patch("retry_stale_scrapes.detect_holding_changes") as changes, \
        patch("retry_stale_scrapes.generate_signal_report") as signal_report:
        summary = retry_stale_etfs(db_path=db_path, run_date="2026-07-07", report_dir=tmp_path)

    assert summary["stale_before"] == 1
    assert summary["stale_after"] == 1
    assert summary["improved"] is False
    assert summary["reports_overwritten"] is False
    changes.assert_not_called()
    signal_report.assert_not_called()


def test_retry_stale_etfs_does_not_overwrite_when_stale_count_does_not_drop(tmp_path):
    db_path = _init_db(tmp_path)
    _seed_etf("00401A")
    _insert_scrape_run("00401A", data_date="2026-07-06", status="stale")

    retry_summary = {
        "date": "2026-07-07",
        "total_etfs": 1,
        "data_freshness": {"fresh": 0, "stale": 1, "unknown": 0},
        "stale_etfs": [{"etf_code": "00401A", "data_date": "2026-07-06"}],
    }

    with patch("retry_stale_scrapes.run_selected_scrape_with_browser", return_value=retry_summary), \
        patch("retry_stale_scrapes.detect_holding_changes") as changes, \
        patch("retry_stale_scrapes.generate_signal_report") as signal_report:
        summary = retry_stale_etfs(db_path=db_path, run_date="2026-07-07", report_dir=tmp_path)

    assert summary["stale_before"] == 1
    assert summary["stale_after"] == 1
    assert summary["improved"] is False
    assert summary["reports_overwritten"] is False
    changes.assert_not_called()
    signal_report.assert_not_called()
    assert not (tmp_path / "taiwan_active_etf_signal_report_2026-07-07.txt").exists()


def test_retry_stale_etfs_noops_when_no_stale_rows(tmp_path):
    db_path = _init_db(tmp_path)
    _seed_etf("00401A")
    _insert_scrape_run("00401A", data_date="2026-07-07", status="success")

    with patch("retry_stale_scrapes.run_selected_scrape_with_browser") as scrape:
        summary = retry_stale_etfs(db_path=db_path, run_date="2026-07-07", report_dir=tmp_path)

    scrape.assert_not_called()
    assert summary["retried_etfs"] == []
    assert summary["stale_before"] == 0
    assert summary["stale_after"] == 0
    assert summary["reports_overwritten"] is False
