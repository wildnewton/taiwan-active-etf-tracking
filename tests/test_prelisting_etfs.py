from datetime import date, datetime
from unittest.mock import patch

import pytest

import db
import pipeline
import report
from discover_active_etfs import parse_security_master
from etf_universe import (
    get_active_etf_count,
    get_active_etfs,
    get_etf_config,
    reconcile_discovered_universe,
    seed_etf_universe_from_file,
)


FUTURE_ETF = {
    "code": "00408A",
    "name": "主動第一金優股息",
    "market": "TWSE",
    "isin": "TW00000408A0",
    "listing_date": "2026-07-15",
}
RUN_AT = datetime(2026, 7, 14, 15, 0, tzinfo=pipeline.TAIPEI_TIMEZONE)
RUN_DATE = RUN_AT.date()


def _seed_with_future_etf():
    seed_etf_universe_from_file(seen_date="2026-07-14")
    reconcile_discovered_universe(
        [FUTURE_ETF],
        seen_date="2026-07-14",
        discovery_complete=False,
    )


def _failed_scrape_result():
    return {
        "ok": False,
        "reason": "test",
        "all_rows": [],
        "stock_rows": [],
        "non_stock_rows": [],
        "source_url": "",
        "source_type": "",
        "total_weight_all_rows": 0.0,
        "total_weight_stock_rows": 0.0,
    }


def _insert_scrape_run(status, data_date=None):
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_scrape_runs (
                date, data_date, etf_code, status, primary_source, primary_success,
                moneydj_browser_used, official_fallback_used, official_success,
                rows_extracted, stock_rows_extracted, non_stock_rows_extracted,
                total_weight_all_rows, total_weight_stock_rows, source_url, error,
                started_at, finished_at
            ) VALUES (?, ?, ?, ?, 'moneydj_primary', 1, 0, 0, 0,
                1, 1, 0, 80.0, 80.0, 'https://test', NULL,
                '2026-07-14T20:00:00', '2026-07-14T20:01:00')
            """,
            ("2026-07-14", data_date, "00408A", status),
        )


def test_init_db_owns_listing_date_column():
    db.init_db(":memory:")
    columns = {
        row[1]
        for row in db._connect().execute("PRAGMA table_info(etf_universe)").fetchall()
    }
    assert "listing_date" in columns


def test_security_master_extracts_and_normalizes_listing_date():
    html = """
    <table>
      <tr><td>00408A 主動第一金優股息</td><td>TW00000408A0</td><td>2026/07/15</td></tr>
      <tr><td>00409A 主動測試ETF</td><td>TW00000409A8</td><td>2026-07-16</td></tr>
    </table>
    """
    securities = parse_security_master(html, "TWSE")
    assert [row.as_dict()["listing_date"] for row in securities] == [
        "2026-07-15",
        "2026-07-16",
    ]


def test_universe_persists_and_filters_listing_date():
    db.init_db(":memory:")
    _seed_with_future_etf()
    assert get_etf_config("00408A")["listing_date"] == "2026-07-15"
    assert "00408A" not in {
        row["code"] for row in get_active_etfs(as_of_date="2026-07-14")
    }
    assert "00408A" in {
        row["code"] for row in get_active_etfs(as_of_date="2026-07-15")
    }
    assert get_active_etf_count(as_of_date="2026-07-14") == 19
    assert get_active_etf_count(as_of_date="2026-07-15") == 20
    assert get_etf_config("00408A")["retired"] == 0


def test_unknown_listing_date_remains_eligible():
    db.init_db(":memory:")
    seed_etf_universe_from_file(seen_date="2026-07-14")

    active_codes = {
        row["code"] for row in get_active_etfs(as_of_date="2026-07-14")
    }

    assert "00980A" in active_codes


def test_sync_pipeline_uses_same_taipei_run_date_for_universe(tmp_path):
    db_path = str(tmp_path / "universe.sqlite")
    db.init_db(db_path)
    _seed_with_future_etf()
    seen_codes = []

    def fake_scrape(code, target_date):
        seen_codes.append(code)
        assert target_date == RUN_DATE
        return _failed_scrape_result()

    with patch("pipeline._current_run_at", return_value=RUN_AT), \
        patch("etf_universe._today", return_value="2026-07-15"), \
        patch("pipeline.is_tw_trading_day", return_value=True), \
        patch("pipeline.latest_tw_trading_day_on_or_before", return_value=RUN_DATE), \
        patch("pipeline.scrape_holdings", side_effect=fake_scrape), \
        patch("pipeline._check_moneydj_warning"):
        summary = pipeline.run_daily_scrape(db_path)

    assert "00408A" not in seen_codes
    assert summary["total_etfs"] == 19


@pytest.mark.asyncio
async def test_async_pipeline_uses_same_taipei_run_date_for_universe(tmp_path):
    db_path = str(tmp_path / "universe.sqlite")
    db.init_db(db_path)
    _seed_with_future_etf()
    seen_codes = []

    async def fake_scrape(code, target_date):
        seen_codes.append(code)
        assert target_date == RUN_DATE
        return _failed_scrape_result()

    with patch("pipeline._current_run_at", return_value=RUN_AT), \
        patch("etf_universe._today", return_value="2026-07-15"), \
        patch("pipeline.is_tw_trading_day", return_value=True), \
        patch("pipeline.latest_tw_trading_day_on_or_before", return_value=RUN_DATE), \
        patch("pipeline._check_moneydj_warning"):
        summary = await pipeline._run_scrape_async(db_path, None, fake_scrape)

    assert "00408A" not in seen_codes
    assert summary["total_etfs"] == 19


def test_report_uses_historical_listing_boundary_for_denominator_and_runs():
    db.init_db(":memory:")
    _seed_with_future_etf()
    _insert_scrape_run("failed")

    quality = report._get_data_quality("2026-07-14")

    assert quality["expected_count"] == 19
    assert "00408A" not in quality["failed_etfs"]


def test_report_excludes_prelisting_freshness_and_change_diagnostics():
    db.init_db(":memory:")
    _seed_with_future_etf()
    _insert_scrape_run("success", data_date="2026-07-14")
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_change_diagnostics (
                date, prev_date, etf_code, status, reason, created_at
            ) VALUES (?, ?, ?, 'skipped', 'test', ?)
            """,
            ("2026-07-14", "2026-07-13", "00408A", "2026-07-14T21:00:00"),
        )

    assert report._get_scrape_data_freshness("2026-07-14") == {
        "fresh": [],
        "stale": [],
        "unknown": [],
    }
    assert report._get_skipped_change_diagnostics("2026-07-14") == []
