from datetime import date, datetime
from unittest.mock import AsyncMock, patch

import pytest

import pipeline
from pipeline import run_daily_scrape, run_daily_scrape_with_browser_async
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


def _write_params(path):
    path.write_text(
        """
HOLIDAYS = {
    2026: {
        "TW": {"0619", "1009"},
        "CN": set(),
    }
}
""".strip(),
        encoding="utf-8",
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


def test_tw_stock_calendar_uses_params_holidays_and_weekends(tmp_path):
    params_path = tmp_path / "params.py"
    _write_params(params_path)

    assert latest_tw_trading_day_on_or_before(
        date(2026, 6, 20), params_path=params_path
    ) == date(2026, 6, 18)
    assert latest_tw_trading_day_on_or_before(
        date(2026, 6, 29), params_path=params_path
    ) == date(2026, 6, 29)
    assert is_tw_trading_day(date(2026, 6, 19), params_path=params_path) is False
    assert is_tw_trading_day(date(2026, 6, 20), params_path=params_path) is False
    assert is_tw_trading_day(date(2026, 6, 29), params_path=params_path) is True


def test_tw_stock_calendar_uses_params_non_trading_day_overrides(tmp_path):
    params_path = tmp_path / "params.py"
    params_path.write_text(
        """
HOLIDAYS = {
    2026: {
        "TW": {"0619"},
        "CN": set(),
    }
}
NON_TRADING_DAYS = {
    2026: {
        "TW": {"0710"},
        "CN": set(),
    }
}
""".strip(),
        encoding="utf-8",
    )

    assert latest_tw_trading_day_on_or_before(
        date(2026, 7, 10), params_path=params_path
    ) == date(2026, 7, 9)
    assert is_tw_trading_day(date(2026, 7, 10), params_path=params_path) is False
    assert is_tw_trading_day(date(2026, 7, 13), params_path=params_path) is True


def test_tw_stock_calendar_missing_params_returns_unknown(tmp_path):
    missing = tmp_path / "missing_params.py"

    assert latest_tw_trading_day_on_or_before(
        date(2026, 6, 28), params_path=missing
    ) is None
    assert is_tw_trading_day(date(2026, 6, 28), params_path=missing) is None


def test_daily_scrape_recovers_only_missing_snapshot_on_non_trading_day():
    def scrape_result(code, target_date):
        return make_success(code, target_date.strftime("%Y/%m/%d"))

    with patch("pipeline.date", NonTradingRunDate), patch(
        "pipeline._current_run_at",
        return_value=datetime.combine(
            NonTradingRunDate.today(),
            pipeline.DATA_AVAILABILITY_CUTOFF,
            tzinfo=pipeline.TAIPEI_TIMEZONE,
        ),
    ), patch("pipeline.is_tw_trading_day", return_value=False), patch(
        "pipeline._active_etfs_for_run", return_value=ETFS
    ), patch(
        "pipeline.latest_tw_trading_day_on_or_before",
        return_value=LAST_TRADING_DATE,
    ), patch(
        "pipeline.snapshot_exists",
        side_effect=lambda _date, code: code == "00980A",
    ), patch(
        "pipeline.scrape_holdings", side_effect=scrape_result
    ) as scrape_holdings, patch("pipeline.init_db"), patch(
        "pipeline.replace_daily_snapshot", return_value={"inserted": True}
    ):
        summary = run_daily_scrape(":memory:")

    scrape_holdings.assert_called_once_with("00981A", LAST_TRADING_DATE)
    assert summary["date"] == "2026-06-27"
    assert summary["expected_data_date"] == "2026-06-26"
    assert summary["is_trading_day"] is False
    assert summary["preexisting_success"] == 1
    assert summary["moneydj_success"] == 1
    assert summary["data_freshness"] == {"fresh": 2, "stale": 0, "unknown": 0}


@pytest.mark.asyncio
async def test_daily_browser_scrape_recovers_only_missing_snapshot_on_non_trading_day():
    page = object()
    scraper = AsyncMock(
        return_value=make_success("00981A", LAST_TRADING_DATE.strftime("%Y/%m/%d"))
    )

    with patch("pipeline.date", NonTradingRunDate), patch(
        "pipeline._current_run_at",
        return_value=datetime.combine(
            NonTradingRunDate.today(),
            pipeline.DATA_AVAILABILITY_CUTOFF,
            tzinfo=pipeline.TAIPEI_TIMEZONE,
        ),
    ), patch("pipeline.is_tw_trading_day", return_value=False), patch(
        "pipeline._active_etfs_for_run", return_value=ETFS
    ), patch(
        "pipeline.latest_tw_trading_day_on_or_before",
        return_value=LAST_TRADING_DATE,
    ), patch(
        "pipeline.snapshot_exists",
        side_effect=lambda _date, code: code == "00980A",
    ), patch(
        "pipeline.scrape_holdings_with_browser_async", scraper
    ), patch("pipeline.init_db"), patch(
        "pipeline.replace_daily_snapshot", return_value={"inserted": True}
    ):
        summary = await run_daily_scrape_with_browser_async(":memory:", page=page)

    scraper.assert_awaited_once_with(
        "00981A",
        page,
        target_date=LAST_TRADING_DATE,
    )
    assert summary["expected_data_date"] == "2026-06-26"
    assert summary["is_trading_day"] is False
    assert summary["preexisting_success"] == 1
    assert summary["moneydj_success"] == 1


@pytest.mark.asyncio
async def test_daily_browser_scrape_non_trading_day_all_preexisting_skips_playwright():
    with patch("pipeline.date", NonTradingRunDate), patch(
        "pipeline._current_run_at",
        return_value=datetime.combine(
            NonTradingRunDate.today(),
            pipeline.DATA_AVAILABILITY_CUTOFF,
            tzinfo=pipeline.TAIPEI_TIMEZONE,
        ),
    ), patch("pipeline.is_tw_trading_day", return_value=False), patch(
        "pipeline._active_etfs_for_run", return_value=ETFS
    ), patch(
        "pipeline.latest_tw_trading_day_on_or_before",
        return_value=LAST_TRADING_DATE,
    ), patch(
        "pipeline.snapshot_exists", return_value=True
    ), patch(
        "pipeline.init_db"
    ), patch(
        "playwright.async_api.async_playwright",
        side_effect=AssertionError("Playwright must not start"),
    ) as async_playwright:
        summary = await run_daily_scrape_with_browser_async(":memory:")

    async_playwright.assert_not_called()
    assert summary["preexisting_success"] == 2
    assert summary["data_freshness"] == {"fresh": 2, "stale": 0, "unknown": 0}


def test_daily_scrape_runs_when_run_date_is_tw_trading_day():
    with patch("pipeline.date", TradingRunDate), patch(
        "pipeline._current_run_at",
        return_value=datetime.combine(
            TradingRunDate.today(),
            pipeline.DATA_AVAILABILITY_CUTOFF,
            tzinfo=pipeline.TAIPEI_TIMEZONE,
        ),
    ), patch("pipeline.is_tw_trading_day", return_value=True), patch(
        "pipeline._active_etfs_for_run", return_value=ETFS
    ), patch(
        "pipeline.latest_tw_trading_day_on_or_before", return_value=TRADING_DATE
    ), patch(
        "pipeline.snapshot_exists", return_value=False
    ), patch(
        "pipeline.scrape_holdings",
        side_effect=lambda code, target_date=None: make_success(code),
    ) as scrape_holdings, patch("pipeline.init_db"), patch(
        "pipeline.replace_daily_snapshot", return_value={"inserted": True}
    ):
        summary = run_daily_scrape(":memory:")

    assert scrape_holdings.call_count == 2
    assert summary["expected_data_date"] == "2026-06-29"
    assert summary["is_trading_day"] is True
    assert summary["preexisting_success"] == 0
    assert summary["data_freshness"] == {"fresh": 2, "stale": 0, "unknown": 0}
