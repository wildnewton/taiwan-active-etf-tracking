from datetime import date
from unittest.mock import AsyncMock, Mock, patch

import pytest

import pipeline
import scraper
import test_live_integration as live


TARGET_DATE = date(2026, 8, 20)
WINDOWS_CHROME_126_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
EXPECTED_BROWSER_CONTEXT_OPTIONS = {
    "locale": "zh-TW",
    "user_agent": WINDOWS_CHROME_126_USER_AGENT,
}


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _FakePage:
    def __init__(self):
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
        page = _FakePage()
        self.pages.append(page)
        return page


def _result(etf_code, source_type):
    row = {
        "date": TARGET_DATE.isoformat(),
        "etf_code": etf_code,
        "asset_name": "台積電(2330.TW)",
        "asset_type": "stock",
        "stock_code": "2330",
        "stock_name": "台積電",
        "shares": 1000,
        "weight_pct": 10.0,
        "source_url": "https://example.test/holdings",
        "source_type": source_type,
        "extraction_method": "test",
    }
    return {
        "ok": True,
        "reason": "ok",
        "all_rows": [row],
        "stock_rows": [row],
        "non_stock_rows": [],
        "source_url": row["source_url"],
        "source_type": source_type,
        "total_weight_all_rows": 10.0,
        "total_weight_stock_rows": 10.0,
    }


def _failure(reason):
    return {**scraper.FAILED_RESULT, "reason": reason}


def _prepared_run(etf_code):
    summary = pipeline._new_summary(
        TARGET_DATE,
        1,
        expected_data_date=TARGET_DATE,
        is_trading_day=True,
    )
    return TARGET_DATE, TARGET_DATE, summary, [{"code": etf_code}]


def test_production_browser_context_options_are_verified_and_canonical():
    assert pipeline.PRODUCTION_BROWSER_CONTEXT_OPTIONS == (
        EXPECTED_BROWSER_CONTEXT_OPTIONS
    )


@pytest.mark.asyncio
async def test_nightly_browser_context_uses_canonical_options_for_moneydj_browser():
    etf_code = "00980A"
    browser_stack = _FakeBrowserStack()
    moneydj_browser_result = _result(etf_code, "moneydj_browser")

    with patch(
        "pipeline._prepare_scrape_run",
        return_value=_prepared_run(etf_code),
    ), patch(
        "playwright.async_api.async_playwright",
        new=browser_stack.async_playwright,
    ), patch(
        "scraper._retry_moneydj_async",
        new=AsyncMock(return_value=_failure("MoneyDJ static failed")),
    ), patch(
        "scraper.scrape_moneydj_browser",
        new=AsyncMock(return_value=moneydj_browser_result),
    ) as moneydj_browser, patch(
        "scraper._official_fallback_with_browser",
        new=AsyncMock(side_effect=AssertionError("official route must not run")),
    ) as official, patch(
        "scraper.get_historical_mean_stock_row_count",
        return_value=None,
    ), patch(
        "pipeline._record_result",
    ):
        await pipeline.run_daily_scrape_with_browser_async("unused.sqlite")

    moneydj_browser.assert_awaited_once_with(etf_code, browser_stack.pages[0])
    official.assert_not_awaited()
    browser_stack.browser.new_context.assert_awaited_once_with(
        **EXPECTED_BROWSER_CONTEXT_OPTIONS
    )


@pytest.mark.asyncio
async def test_selected_browser_context_uses_canonical_options_for_nomura_official():
    etf_code = "00980A"
    browser_stack = _FakeBrowserStack()
    official_result = _result(etf_code, "official_fallback")

    with patch(
        "pipeline._prepare_scrape_run",
        return_value=_prepared_run(etf_code),
    ), patch(
        "playwright.async_api.async_playwright",
        new=browser_stack.async_playwright,
    ), patch(
        "scraper._retry_moneydj_async",
        new=AsyncMock(return_value=_failure("MoneyDJ static failed")),
    ), patch(
        "scraper.scrape_moneydj_browser",
        new=AsyncMock(return_value=_failure("MoneyDJ browser failed")),
    ) as moneydj_browser, patch(
        "scraper.get_etf_config",
        return_value={"official_method": "stealth_api", "issuer": "Nomura"},
    ), patch(
        "scraper.scrape_official_with_browser",
        new=AsyncMock(return_value=official_result),
    ) as official, patch(
        "scraper._official_fallback_static",
        side_effect=AssertionError("static official route must not run"),
    ), patch(
        "pipeline._record_result",
    ):
        await pipeline.run_selected_scrape_with_browser_async(
            "unused.sqlite",
            [etf_code],
            run_date=TARGET_DATE,
        )

    moneydj_browser.assert_awaited_once_with(etf_code, browser_stack.pages[0])
    official.assert_awaited_once_with(etf_code, browser_stack.pages[0])
    browser_stack.browser.new_context.assert_awaited_once_with(
        **EXPECTED_BROWSER_CONTEXT_OPTIONS
    )


def test_live_browser_page_consumes_production_owned_options(monkeypatch):
    sentinel_options = {
        "locale": "fixture-contract-locale",
        "user_agent": "fixture-contract-user-agent",
    }
    monkeypatch.setattr(
        pipeline,
        "PRODUCTION_BROWSER_CONTEXT_OPTIONS",
        sentinel_options,
        raising=False,
    )
    page = object()
    browser = Mock()
    browser.new_page = AsyncMock(return_value=page)
    browser.close = AsyncMock()
    playwright = Mock()
    playwright.chromium.launch = AsyncMock(return_value=browser)
    playwright.stop = AsyncMock()
    starter = Mock()
    starter.start = AsyncMock(return_value=playwright)

    with patch(
        "playwright.async_api.async_playwright",
        return_value=starter,
    ):
        fixture = live._browser_context.__wrapped__()
        actual_page, loop = next(fixture)
        try:
            assert actual_page is page
            assert loop is not None
            browser.new_page.assert_awaited_once_with(**sentinel_options)
        finally:
            with pytest.raises(StopIteration):
                next(fixture)
