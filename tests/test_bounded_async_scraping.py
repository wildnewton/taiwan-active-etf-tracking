import asyncio
from datetime import date, datetime
from unittest.mock import AsyncMock, Mock, call, patch

import pytest

import pipeline
import scraper


RUN_DATE = date(2026, 7, 15)
ETFS = [{"code": f"ETF{i}"} for i in range(1, 5)]


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


def _failure(reason: str = "temporary failure") -> dict:
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


def _prepared_run(etfs=ETFS):
    summary = pipeline._new_summary(
        RUN_DATE,
        len(etfs),
        expected_data_date=RUN_DATE,
        is_trading_day=True,
    )
    return RUN_DATE, RUN_DATE, summary, list(etfs)


@pytest.mark.asyncio
async def test_async_retry_offloads_all_attempts_and_uses_async_backoff():
    failure = _failure()

    with patch(
        "scraper.asyncio.to_thread",
        new=AsyncMock(return_value=failure),
    ) as to_thread, patch(
        "scraper.asyncio.sleep",
        new=AsyncMock(),
    ) as async_sleep, patch("scraper.time.sleep") as sync_sleep:
        result = await scraper._retry_moneydj_async("00980A")

    assert result == failure
    assert to_thread.await_count == scraper._MONEYDJ_RETRIES
    assert to_thread.await_args_list == [
        call(scraper.scrape_moneydj, "00980A")
        for _ in range(scraper._MONEYDJ_RETRIES)
    ]
    assert [item.args[0] for item in async_sleep.await_args_list] == (
        scraper._MONEYDJ_RETRY_DELAYS
    )
    sync_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_async_retry_stops_immediately_for_warned_valid_result():
    warned = {
        **_success("00980A"),
        "weight_warning": {
            "reason": "total_weight_below_expected_range",
            "source_total_weight_all_rows": 61.98,
            "minimum_expected_weight": 70.0,
            "maximum_expected_weight": 140.0,
        },
    }

    with patch(
        "scraper.asyncio.to_thread",
        new=AsyncMock(return_value=warned),
    ) as to_thread, patch(
        "scraper.asyncio.sleep",
        new=AsyncMock(),
    ) as async_sleep:
        result = await scraper._retry_moneydj_async("00980A")

    assert result is warned
    to_thread.assert_awaited_once_with(scraper.scrape_moneydj, "00980A")
    async_sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_browser_decision_tree_uses_async_moneydj_retry():
    result = _success("00980A")

    with patch(
        "scraper._retry_moneydj_async",
        new=AsyncMock(return_value=result),
    ) as async_retry, patch(
        "scraper._retry_moneydj",
        side_effect=AssertionError("sync retry must not run in async path"),
    ), patch(
        "scraper.get_historical_mean_stock_row_count",
        return_value=None,
    ):
        actual = await scraper.scrape_holdings_with_browser_async(
            "00980A",
            object(),
            target_date=RUN_DATE,
        )

    async_retry.assert_awaited_once_with("00980A")
    assert actual["ok"] is True


@pytest.mark.asyncio
async def test_production_browser_run_limits_concurrency_and_owns_pages_per_worker():
    browser_stack = _FakeBrowserStack()
    active = 0
    max_active = 0
    completion_order = []
    recording_order = []
    seen_pages = {}
    delays = {"ETF1": 0.04, "ETF2": 0.01, "ETF3": 0.02, "ETF4": 0.0}

    async def scrape_one(etf_code, page, target_date):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        seen_pages[etf_code] = page
        try:
            await asyncio.sleep(delays[etf_code])
            completion_order.append(etf_code)
            return _success(etf_code)
        finally:
            active -= 1

    def record_result(summary, etf_code, run_date, expected_date, started_at, finished_at, result):
        recording_order.append(etf_code)

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

    assert max_active == 3
    assert completion_order != [etf["code"] for etf in ETFS]
    assert recording_order == [etf["code"] for etf in ETFS]
    assert len(browser_stack.pages) == len(ETFS)
    assert len({id(page) for page in seen_pages.values()}) == len(ETFS)
    assert set(seen_pages.values()) == set(browser_stack.pages)
    for page in browser_stack.pages:
        page.close.assert_awaited_once_with()
    browser_stack.context.close.assert_awaited_once_with()
    browser_stack.browser.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_worker_exception_becomes_one_failure_without_cancelling_other_etfs():
    browser_stack = _FakeBrowserStack()
    etfs = [{"code": "ETF1"}, {"code": "ETF2"}, {"code": "ETF3"}]
    completed = []
    recorded = []

    async def scrape_one(etf_code, page, target_date):
        await asyncio.sleep(0)
        if etf_code == "ETF2":
            raise RuntimeError("worker exploded")
        completed.append(etf_code)
        return _success(etf_code)

    def record_result(summary, etf_code, run_date, expected_date, started_at, finished_at, result):
        recorded.append((etf_code, result["ok"], result.get("reason")))

    with patch(
        "pipeline._prepare_scrape_run",
        return_value=_prepared_run(etfs),
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

    assert set(completed) == {"ETF1", "ETF3"}
    assert [item[0] for item in recorded] == ["ETF1", "ETF2", "ETF3"]
    assert recorded[0][1] is True
    assert recorded[1][1] is False
    assert "worker exploded" in recorded[1][2]
    assert recorded[2][1] is True
    for page in browser_stack.pages:
        page.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_returned_failure_still_closes_worker_page():
    browser_stack = _FakeBrowserStack()
    etfs = [{"code": "ETF1"}]
    recorded = []

    async def scrape_one(etf_code, page, target_date):
        return _failure("returned failure")

    def record_result(summary, etf_code, run_date, expected_date, started_at, finished_at, result):
        recorded.append((etf_code, result["ok"], result.get("reason")))

    with patch(
        "pipeline._prepare_scrape_run",
        return_value=_prepared_run(etfs),
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

    assert recorded == [("ETF1", False, "returned failure")]
    browser_stack.pages[0].close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_page_close_exception_becomes_one_failure_without_aborting_other_etfs():
    browser_stack = _FakeBrowserStack()
    etfs = [{"code": "ETF1"}, {"code": "ETF2"}, {"code": "ETF3"}]
    recorded = []
    original_new_page = browser_stack._new_page

    async def new_page_with_one_close_failure():
        page = await original_new_page()
        if len(browser_stack.pages) == 2:
            page.close = AsyncMock(side_effect=RuntimeError("page close exploded"))
        return page

    browser_stack.context.new_page = AsyncMock(
        side_effect=new_page_with_one_close_failure
    )

    async def scrape_one(etf_code, page, target_date):
        await asyncio.sleep(0)
        return _success(etf_code)

    def record_result(summary, etf_code, run_date, expected_date, started_at, finished_at, result):
        recorded.append((etf_code, result["ok"], result.get("reason")))

    with patch(
        "pipeline._prepare_scrape_run",
        return_value=_prepared_run(etfs),
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
    assert recorded[0][1] is True
    assert recorded[1][1] is False
    assert "page close exploded" in recorded[1][2]
    assert recorded[2][1] is True
    for page in browser_stack.pages:
        page.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_injected_page_path_remains_sequential():
    shared_page = object()
    active = 0
    max_active = 0
    calls = []

    async def scrape_one(etf_code, page, target_date):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        calls.append((etf_code, page))
        await asyncio.sleep(0)
        active -= 1
        return _success(etf_code)

    with patch(
        "pipeline._prepare_scrape_run",
        return_value=_prepared_run(),
    ), patch(
        "pipeline.scrape_holdings_with_browser_async",
        new=scrape_one,
    ), patch("pipeline._record_result"):
        await pipeline.run_daily_scrape_with_browser_async(
            "unused.sqlite",
            page=shared_page,
        )

    assert max_active == 1
    assert calls == [(etf["code"], shared_page) for etf in ETFS]
