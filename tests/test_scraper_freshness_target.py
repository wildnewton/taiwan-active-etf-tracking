from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

import scraper
from pipeline import run_daily_scrape_with_browser_async, run_selected_scrape_with_browser_async


TARGET_DATE = date(2026, 7, 13)
STALE_DATE = date(2026, 7, 9)
ETF_CODE = "00980A"


class FixedDate(date):
    @classmethod
    def today(cls):
        return cls(TARGET_DATE.year, TARGET_DATE.month, TARGET_DATE.day)


def make_result(data_date: date, source_type: str = "moneydj_primary") -> dict:
    row = {
        "date": data_date.isoformat(),
        "etf_code": ETF_CODE,
        "asset_name": "台積電(2330.TW)",
        "asset_type": "stock",
        "stock_code": "2330",
        "stock_name": "台積電",
        "shares": 1000,
        "weight_pct": 10.0,
        "source_url": "https://example.test",
        "source_type": source_type,
        "extraction_method": "test",
    }
    return {
        "ok": True,
        "reason": "ok",
        "all_rows": [row],
        "stock_rows": [row],
        "non_stock_rows": [],
        "source_url": "https://example.test",
        "source_type": source_type,
        "total_weight_all_rows": 10.0,
        "total_weight_stock_rows": 10.0,
    }


def test_sync_scraper_uses_caller_target_and_skips_fallback_when_fresh():
    moneydj = make_result(TARGET_DATE)

    with patch("scraper._retry_moneydj", return_value=moneydj), \
        patch("scraper._official_fallback_static") as official_fallback, \
        patch("scraper.get_historical_mean_stock_row_count", return_value=None):
        result = scraper.scrape_holdings(ETF_CODE, target_date=TARGET_DATE)

    assert result["source_type"] == "moneydj_primary"
    official_fallback.assert_not_called()


def test_sync_scraper_uses_caller_target_and_falls_back_when_stale():
    moneydj = make_result(STALE_DATE)
    official = make_result(TARGET_DATE, source_type="official_fallback")

    with patch("scraper._retry_moneydj", return_value=moneydj), \
        patch("scraper._official_fallback_static", return_value=official) as official_fallback, \
        patch("scraper.get_historical_mean_stock_row_count", return_value=None):
        result = scraper.scrape_holdings(ETF_CODE, target_date=TARGET_DATE)

    official_fallback.assert_called_once_with(ETF_CODE)
    assert result["source_type"] == "official_fallback"
    assert result["all_rows"][0]["date"] == TARGET_DATE.isoformat()


@pytest.mark.asyncio
async def test_browser_scraper_uses_caller_target_for_official_fallback():
    moneydj = make_result(STALE_DATE)
    official = make_result(TARGET_DATE, source_type="official_fallback")
    official_fallback = AsyncMock(return_value=official)

    with patch("scraper._retry_moneydj_async", new=AsyncMock(return_value=moneydj)), \
        patch("scraper._official_fallback_with_browser", new=official_fallback), \
        patch("scraper.get_historical_mean_stock_row_count", return_value=None):
        result = await scraper.scrape_holdings_with_browser_async(
            ETF_CODE,
            object(),
            target_date=TARGET_DATE,
        )

    official_fallback.assert_awaited_once()
    assert result["source_type"] == "official_fallback"


@pytest.mark.asyncio
async def test_daily_pipeline_passes_expected_data_date_to_browser_scraper():
    page = object()

    with patch("pipeline.date", FixedDate), \
        patch("pipeline._active_etfs_for_run", return_value=[{"code": ETF_CODE}]), \
        patch("pipeline.latest_tw_trading_day_on_or_before", return_value=TARGET_DATE), \
        patch("pipeline.successful_snapshot_exists", return_value=False), \
        patch("pipeline.scrape_holdings_with_browser_async", autospec=True) as scraper_mock, \
        patch("pipeline.init_db"), \
        patch("pipeline.replace_daily_snapshot", return_value={"inserted": True}), \
        patch("pipeline.insert_scrape_run"):
        scraper_mock.return_value = make_result(TARGET_DATE)
        await run_daily_scrape_with_browser_async(":memory:", page=page)

    scraper_mock.assert_awaited_once_with(ETF_CODE, page, target_date=TARGET_DATE)


@pytest.mark.asyncio
async def test_selected_pipeline_passes_explicit_run_date_as_target():
    page = object()
    explicit_run_date = date(2026, 7, 6)

    with patch("pipeline.scrape_holdings_with_browser_async", autospec=True) as scraper_mock, \
        patch("pipeline.init_db"), \
        patch("pipeline.replace_daily_snapshot", return_value={"inserted": True}), \
        patch("pipeline.insert_scrape_run"):
        scraper_mock.return_value = make_result(explicit_run_date)
        await run_selected_scrape_with_browser_async(
            ":memory:",
            [ETF_CODE],
            page=page,
            run_date=explicit_run_date,
        )

    scraper_mock.assert_awaited_once_with(
        ETF_CODE,
        page,
        target_date=explicit_run_date,
    )
