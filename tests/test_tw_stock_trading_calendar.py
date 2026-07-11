import sqlite3
from datetime import date
from unittest.mock import patch

from pipeline import run_daily_scrape
from trading_calendar import is_tw_trading_day, latest_tw_trading_day_on_or_before


RUN_DATE = date(2026, 6, 27)
LAST_TRADING_DATE = date(2026, 6, 26)
TRADING_DATE = date(2026, 6, 29)
ETFS = [{"code": "00980A"}, {"code": "00981A"}]


class NonTradingRunDate(date):
    @classmethod
    def today(cls):
        return cls(RUN_DATE.year, RUN_DATE.month, RUN_DATE.day)


class TradingRunDate(date):
    @classmethod
    def today(cls):
        return cls(TRADING_DATE.year, TRADING_DATE.month, TRADING_DATE.day)


def _create_tw_stock_db(path):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE StockData (date TEXT NOT NULL, symbol TEXT NOT NULL)")
        conn.executemany(
            "INSERT INTO StockData (date, symbol) VALUES (?, ?)",
            [
                ("2026-06-26", "2330"),
                ("2026-06-26", "2317"),
                ("2026-06-29", "2330"),
            ],
        )


def make_success(etf_code="00980A", row_date="2026/06/29"):
    row = {
        "date": row_date,
        "etf_code": etf_code,
        "asset_name": "台積電(2330.TW)",
        "asset_type": "stock",
        "stock_code": "2330",
        "stock_name": "台積電",
        "shares": 1000,
        "weight_pct": 10.0,
        "source_url": "https://example.test",
        "source_type": "moneydj_primary",
        "extraction_method": "requests_bs4",
    }
    return {
        "ok": True,
        "reason": "ok",
        "all_rows": [row],
        "stock_rows": [row],
        "non_stock_rows": [],
        "source_url": "https://example.test",
        "source_type": "moneydj_primary",
        "total_weight_all_rows": 10.0,
        "total_weight_stock_rows": 10.0,
    }


def test_tw_stock_calendar_returns_latest_trading_day_on_or_before(tmp_path):
    tw_db = tmp_path / "stocks.db"
    _create_tw_stock_db(tw_db)

    assert latest_tw_trading_day_on_or_before(date(2026, 6, 28), db_path=tw_db) == date(2026, 6, 26)
    assert latest_tw_trading_day_on_or_before(date(2026, 6, 29), db_path=tw_db) == date(2026, 6, 29)
    assert is_tw_trading_day(date(2026, 6, 28), db_path=tw_db) is False
    assert is_tw_trading_day(date(2026, 6, 29), db_path=tw_db) is True


def test_tw_stock_calendar_missing_db_returns_unknown(tmp_path):
    missing = tmp_path / "missing.sqlite"

    assert latest_tw_trading_day_on_or_before(date(2026, 6, 28), db_path=missing) is None
    assert is_tw_trading_day(date(2026, 6, 28), db_path=missing) is None


def test_daily_scrape_skips_before_scraping_when_run_date_is_not_tw_trading_day():
    with patch("pipeline.date", NonTradingRunDate), \
        patch("pipeline._active_etfs_for_run", return_value=ETFS), \
        patch("pipeline.latest_tw_trading_day_on_or_before", return_value=LAST_TRADING_DATE), \
        patch("pipeline.scrape_holdings") as scrape_holdings, \
        patch("pipeline.init_db"), \
        patch("pipeline.replace_daily_snapshot") as replace_daily_snapshot, \
        patch("pipeline.insert_scrape_run") as insert_scrape_run:
        summary = run_daily_scrape(":memory:")

    scrape_holdings.assert_not_called()
    replace_daily_snapshot.assert_not_called()
    insert_scrape_run.assert_not_called()
    assert summary["date"] == "2026-06-27"
    assert summary["expected_data_date"] == "2026-06-26"
    assert summary["is_trading_day"] is False
    assert summary["skipped_non_trading_day"] == 2
    assert summary["skip_reason"] == "tw_stock_market_closed"


def test_daily_scrape_runs_when_run_date_is_tw_trading_day():
    with patch("pipeline.date", TradingRunDate), \
        patch("pipeline._active_etfs_for_run", return_value=ETFS), \
        patch("pipeline.latest_tw_trading_day_on_or_before", return_value=TRADING_DATE), \
        patch("pipeline.scrape_holdings", side_effect=lambda code: make_success(code)) as scrape_holdings, \
        patch("pipeline.init_db"), \
        patch("pipeline.replace_daily_snapshot", return_value={"inserted": True}), \
        patch("pipeline.insert_scrape_run"):
        summary = run_daily_scrape(":memory:")

    assert scrape_holdings.call_count == 2
    assert summary["expected_data_date"] == "2026-06-29"
    assert summary["is_trading_day"] is True
    assert summary["skipped_non_trading_day"] == 0
    assert summary["data_freshness"] == {"fresh": 2, "stale": 0, "unknown": 0}
