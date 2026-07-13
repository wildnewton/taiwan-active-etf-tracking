from datetime import date, datetime
from inspect import getsource
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

import pipeline
import scraper


TAIPEI = ZoneInfo("Asia/Taipei")
RUN_DATE = date(2026, 7, 13)
PREVIOUS_TRADING_DATE = date(2026, 7, 9)
ETF_CODE = "00980A"


class FixedDate(date):
    @classmethod
    def today(cls):
        return cls(RUN_DATE.year, RUN_DATE.month, RUN_DATE.day)


def make_success(data_date: date) -> dict:
    row = {
        "date": data_date.isoformat(),
        "etf_code": ETF_CODE,
        "asset_name": "台積電(2330.TW)",
        "asset_type": "stock",
        "stock_code": "2330",
        "stock_name": "台積電",
        "shares": 1000,
        "weight_pct": 100.0,
        "source_url": "https://example.test",
        "source_type": "moneydj_primary",
        "extraction_method": "test",
    }
    return {
        "ok": True,
        "reason": "ok",
        "all_rows": [row],
        "stock_rows": [row],
        "non_stock_rows": [],
        "source_url": "https://example.test",
        "source_type": "moneydj_primary",
        "total_weight_all_rows": 100.0,
        "total_weight_stock_rows": 100.0,
    }


def test_expected_data_date_uses_previous_trading_day_before_1500():
    run_at = datetime(2026, 7, 13, 14, 59, tzinfo=TAIPEI)

    with patch(
        "pipeline.latest_tw_trading_day_on_or_before",
        return_value=PREVIOUS_TRADING_DATE,
    ) as latest_trading_day:
        result = pipeline._expected_data_date_for_run(run_at, True)

    assert result == PREVIOUS_TRADING_DATE
    latest_trading_day.assert_called_once_with(date(2026, 7, 12))


def test_expected_data_date_uses_run_date_at_1500():
    run_at = datetime(2026, 7, 13, 15, 0, tzinfo=TAIPEI)

    with patch(
        "pipeline.latest_tw_trading_day_on_or_before",
        return_value=RUN_DATE,
    ) as latest_trading_day:
        result = pipeline._expected_data_date_for_run(run_at, True)

    assert result == RUN_DATE
    latest_trading_day.assert_called_once_with(RUN_DATE)


def test_trading_day_before_cutoff_is_not_skipped_and_sync_scraper_gets_previous_date():
    run_at = datetime(2026, 7, 13, 14, 59, tzinfo=TAIPEI)

    with patch("pipeline.date", FixedDate), \
        patch("pipeline._current_run_at", return_value=run_at, create=True) as current_run_at, \
        patch("pipeline.is_tw_trading_day", return_value=True, create=True), \
        patch(
            "pipeline.latest_tw_trading_day_on_or_before",
            return_value=PREVIOUS_TRADING_DATE,
        ), \
        patch("pipeline._active_etfs_for_run", return_value=[{"code": ETF_CODE}]), \
        patch("pipeline.scrape_holdings", return_value=make_success(PREVIOUS_TRADING_DATE)) as scrape_holdings, \
        patch("pipeline.init_db"), \
        patch("pipeline.replace_daily_snapshot", return_value={"inserted": True}), \
        patch("pipeline.insert_scrape_run"):
        summary = pipeline.run_daily_scrape(":memory:")

    current_run_at.assert_called_once_with()
    scrape_holdings.assert_called_once_with(ETF_CODE, PREVIOUS_TRADING_DATE)
    assert summary["is_trading_day"] is True
    assert summary["skip_reason"] is None


def test_daily_run_date_and_cutoff_use_the_same_taipei_clock():
    taipei_run_at = datetime(2026, 7, 14, 0, 30, tzinfo=TAIPEI)
    taipei_date = taipei_run_at.date()

    with patch("pipeline.date", FixedDate), \
        patch("pipeline._current_run_at", return_value=taipei_run_at), \
        patch("pipeline.is_tw_trading_day", return_value=True), \
        patch(
            "pipeline.latest_tw_trading_day_on_or_before",
            return_value=RUN_DATE,
        ), \
        patch("pipeline._active_etfs_for_run", return_value=[{"code": ETF_CODE}]), \
        patch("pipeline.scrape_holdings", return_value=make_success(RUN_DATE)) as scrape_holdings, \
        patch("pipeline.init_db"), \
        patch("pipeline.replace_daily_snapshot", return_value={"inserted": True}), \
        patch("pipeline.insert_scrape_run"):
        summary = pipeline.run_daily_scrape(":memory:")

    assert summary["date"] == taipei_date.isoformat()
    scrape_holdings.assert_called_once_with(ETF_CODE, RUN_DATE)


def test_browser_scrape_adapter_keeps_readable_argument_indentation():
    source = getsource(pipeline._browser_scrape_fn)

    assert (
        "            etf_code,\n"
        "            page,\n"
        "            target_date=target_date,\n"
        "        )"
    ) in source


def test_sync_scraper_rejects_none_target_before_network_work():
    with patch("scraper._retry_moneydj") as retry_moneydj:
        with pytest.raises(TypeError, match="target_date is required"):
            scraper.scrape_holdings(ETF_CODE, target_date=None)

    retry_moneydj.assert_not_called()


def test_browser_wrapper_rejects_none_target_before_async_work():
    with patch("scraper._run_async") as run_async:
        with pytest.raises(TypeError, match="target_date is required"):
            scraper.scrape_holdings_with_browser(
                ETF_CODE,
                object(),
                target_date=None,
            )

    run_async.assert_not_called()


@pytest.mark.asyncio
async def test_async_browser_scraper_rejects_none_target_before_network_work():
    retry_moneydj = AsyncMock()
    with patch("scraper._retry_moneydj", new=retry_moneydj):
        with pytest.raises(TypeError, match="target_date is required"):
            await scraper.scrape_holdings_with_browser_async(
                ETF_CODE,
                object(),
                target_date=None,
            )

    retry_moneydj.assert_not_awaited()
