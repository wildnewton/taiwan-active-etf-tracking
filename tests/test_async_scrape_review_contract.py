import asyncio
from datetime import date
from unittest.mock import AsyncMock, Mock, patch

import pytest

import pipeline
import scraper


RUN_DATE = date(2026, 7, 15)
ETFS = [{"code": "ETF1"}, {"code": "ETF2"}, {"code": "ETF3"}]


def _success(etf_code: str) -> dict:
    row = {
        "date": RUN_DATE.isoformat(),
        "etf_code": etf_code,
        "asset_name": "台積電(2330.TW)",
        "asset_type": "stock",
        "stock_code": "2330",
        "stock_name": "台積電",
        "shares": 1000,
        "weight_pct": 10.0,
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
        "total_weight_all_rows": 10.0,
        "total_weight_stock_rows": 10.0,
    }


def _failure(reason: str) -> dict:
    return {**scraper.FAILED_RESULT, "reason": reason}


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakePage:
    def __init__(self, name: str):
        self.name = name
        self.close = AsyncMock()


class _FakeBrowserStack:
    def __init__(self):
        self.pages = []
        self.context = Mock()
        self.context.new_page = AsyncMock(side_effect=self._new_page)
        self.context.close = AsyncMock()
        self.browser = Mock()
        self.browser.new_context = AsyncMock(return_value=self.context)
        self.browser.close = AsyncMock()
        self.playwright = Mock()
        self.playwright.chromium.launch = AsyncMock(return_value=self.browser)
        self.async_playwright = Mock(return_value=_AsyncContext(self.playwright))

    async def _new_page(self):
        page = _FakePage(f"page-{len(self.pages) + 1}")
        self.pages.append(page)
        return page


def _prepared_run():
    summary = pipeline._new_summary(
        RUN_DATE,
        len(ETFS),
        expected_data_date=RUN_DATE,
        is_trading_day=True,
    )
    return RUN_DATE, RUN_DATE, summary, list(ETFS)


@pytest.mark.asyncio
async def test_async_official_static_fallback_runs_off_event_loop():
    static_result = _success("00405A")

    with patch(
        "scraper.get_etf_config",
        return_value={"official_method": "static"},
    ), patch(
        "scraper.asyncio.to_thread",
        new=AsyncMock(return_value=static_result),
    ) as to_thread, patch(
        "scraper._official_fallback_static",
        side_effect=AssertionError("static fallback must not run on the event loop"),
    ):
        result = await scraper._official_fallback_with_browser("00405A", object())

    assert result is static_result
    to_thread.assert_awaited_once_with(scraper._official_fallback_static, "00405A")


@pytest.mark.asyncio
async def test_page_creation_exception_isolated_without_attempting_missing_page_cleanup():
    browser_stack = _FakeBrowserStack()
    original_new_page = browser_stack._new_page
    creation_attempts = 0
    recorded = []

    async def new_page_with_one_failure():
        nonlocal creation_attempts
        creation_attempts += 1
        if creation_attempts == 1:
            raise RuntimeError("page creation exploded")
        return await original_new_page()

    browser_stack.context.new_page = AsyncMock(side_effect=new_page_with_one_failure)

    async def scrape_one(etf_code, page, target_date):
        await asyncio.sleep(0)
        return _success(etf_code)

    def record_result(
        summary,
        etf_code,
        run_date,
        expected_date,
        started_at,
        finished_at,
        result,
    ):
        recorded.append((etf_code, result["ok"], result.get("reason")))

    with patch(
        "pipeline._prepare_scrape_run",
        return_value=_prepared_run(),
    ), patch(
        "playwright.async_api.async_playwright",
        new=browser_stack.async_playwright,
    ), patch(
        "pipeline.scrape_holdings_with_browser_async",
        new=scrape_one,
    ), patch(
        "pipeline._record_result",
        side_effect=record_result,
    ):
        await pipeline.run_daily_scrape_with_browser_async("unused.sqlite")

    assert [item[0] for item in recorded] == ["ETF1", "ETF2", "ETF3"]
    assert len([item for item in recorded if item[1] is False]) == 1
    assert "page creation exploded" in next(
        item[2] for item in recorded if item[1] is False
    )
    assert len(browser_stack.pages) == 2
    for page in browser_stack.pages:
        page.close.assert_awaited_once_with()
    browser_stack.context.close.assert_awaited_once_with()
    browser_stack.browser.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_page_close_failure_preserves_existing_scrape_failure_reason():
    browser_stack = _FakeBrowserStack()
    recorded = []

    async def scrape_one(etf_code, page, target_date):
        await asyncio.sleep(0)
        if etf_code == "ETF2":
            page.close = AsyncMock(side_effect=RuntimeError("page close exploded"))
            return _failure("scrape failed first")
        return _success(etf_code)

    def record_result(
        summary,
        etf_code,
        run_date,
        expected_date,
        started_at,
        finished_at,
        result,
    ):
        recorded.append((etf_code, result["ok"], result.get("reason")))

    with patch(
        "pipeline._prepare_scrape_run",
        return_value=_prepared_run(),
    ), patch(
        "playwright.async_api.async_playwright",
        new=browser_stack.async_playwright,
    ), patch(
        "pipeline.scrape_holdings_with_browser_async",
        new=scrape_one,
    ), patch(
        "pipeline._record_result",
        side_effect=record_result,
    ):
        await pipeline.run_daily_scrape_with_browser_async("unused.sqlite")

    assert [item[0] for item in recorded] == ["ETF1", "ETF2", "ETF3"]
    assert recorded[1][1] is False
    assert "scrape failed first" in recorded[1][2]
    assert "page close exploded" in recorded[1][2]
    for page in browser_stack.pages:
        page.close.assert_awaited_once_with()
