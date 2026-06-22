from datetime import date
from unittest.mock import patch

import db
from config import TRACKED_ETFS
from pipeline import run_daily_scrape


def make_row(etf_code, asset_type="stock", stock_code="2330", asset_name=None):
    return {
        "date": "2026/06/22",
        "etf_code": etf_code,
        "asset_name": asset_name or f"台積電({stock_code}.TW)",
        "asset_type": asset_type,
        "stock_code": stock_code if asset_type == "stock" else None,
        "stock_name": "台積電" if asset_type == "stock" else None,
        "shares": 1000,
        "weight_pct": 10.0,
        "market_value_twd": None,
        "source_url": "https://example.test",
        "source_type": "moneydj_primary",
        "extraction_method": "requests_bs4",
    }


def make_success(etf_code, source_type="moneydj_primary"):
    stock_row = make_row(etf_code)
    non_stock_row = {
        **make_row(etf_code, asset_type="cash", stock_code=None, asset_name="現金"),
        "source_type": source_type,
    }
    stock_row["source_type"] = source_type
    return {
        "ok": True,
        "reason": "ok",
        "all_rows": [stock_row, non_stock_row],
        "stock_rows": [stock_row],
        "non_stock_rows": [non_stock_row],
        "source_url": "https://example.test",
        "source_type": source_type,
        "total_weight_all_rows": 20.0,
        "total_weight_stock_rows": 10.0,
    }


def make_failure(reason="all sources failed"):
    return {
        "ok": False,
        "reason": reason,
        "all_rows": [],
        "stock_rows": [],
        "non_stock_rows": [],
        "source_url": "",
        "source_type": "",
        "total_weight_all_rows": 0.0,
        "total_weight_stock_rows": 0.0,
    }


def test_run_daily_scrape_all_success():
    with patch("pipeline.scrape_holdings", side_effect=lambda code: make_success(code)) as scrape, \
        patch("pipeline.init_db") as init_db, \
        patch("pipeline.insert_holdings") as insert_holdings, \
        patch("pipeline.insert_non_stock_assets") as insert_non_stock_assets, \
        patch("pipeline.insert_scrape_run") as insert_scrape_run:
        summary = run_daily_scrape(":memory:")

    assert scrape.call_count == 19
    assert summary["date"] == date.today().isoformat()
    assert summary["total_etfs"] == 19
    assert summary["moneydj_success"] == 19
    assert summary["official_success"] == 0
    assert summary["failed"] == 0
    assert summary["total_stock_rows"] == 19
    assert summary["total_non_stock_rows"] == 19
    assert summary["failures"] == []
    init_db.assert_called_once_with(":memory:")
    assert insert_holdings.call_count == 19
    assert insert_non_stock_assets.call_count == 19
    assert insert_scrape_run.call_count == 19


def test_run_daily_scrape_some_fail():
    failed_codes = {TRACKED_ETFS[0]["code"], TRACKED_ETFS[1]["code"]}

    def fake_scrape(code):
        if code in failed_codes:
            return make_failure("blocked")
        return make_success(code, source_type="official_fallback")

    with patch("pipeline.scrape_holdings", side_effect=fake_scrape), \
        patch("pipeline.init_db"), \
        patch("pipeline.insert_holdings"), \
        patch("pipeline.insert_non_stock_assets"), \
        patch("pipeline.insert_scrape_run"):
        summary = run_daily_scrape(":memory:")

    assert summary["total_etfs"] == 19
    assert summary["moneydj_success"] == 0
    assert summary["official_success"] == 17
    assert summary["failed"] == 2
    assert len(summary["failures"]) == 2
    assert {failure["etf_code"] for failure in summary["failures"]} == failed_codes
    assert all(failure["reason"] == "blocked" for failure in summary["failures"])


def test_run_daily_scrape_saves_to_db():
    with patch("pipeline.scrape_holdings", side_effect=lambda code: make_success(code)):
        summary = run_daily_scrape(":memory:")

    with db._connect() as conn:
        holding_count = conn.execute("SELECT COUNT(*) FROM etf_daily_holdings").fetchone()[0]
        non_stock_count = conn.execute(
            "SELECT COUNT(*) FROM etf_daily_non_stock_assets"
        ).fetchone()[0]

    assert summary["total_stock_rows"] == 19
    assert summary["total_non_stock_rows"] == 19
    assert holding_count == 19
    assert non_stock_count == 19


def test_run_daily_scrape_logs_scrape_runs():
    with patch("pipeline.scrape_holdings", side_effect=lambda code: make_success(code)):
        run_daily_scrape(":memory:")

    with db._connect() as conn:
        rows = conn.execute(
            """
            SELECT etf_code, status, primary_source, primary_success
            FROM etf_scrape_runs
            ORDER BY etf_code
            """
        ).fetchall()

    assert len(rows) == 19
    assert {row[1] for row in rows} == {"success"}
    assert {row[2] for row in rows} == {"moneydj_primary"}
    assert {row[3] for row in rows} == {1}
