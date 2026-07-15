from pathlib import Path


TARGET = Path("tests/test_bounded_async_scraping.py")
MARKER = "async def test_async_official_static_fallback_runs_off_event_loop():"
text = TARGET.read_text(encoding="utf-8")
if MARKER in text:
    raise RuntimeError("issue 83 review tests already consolidated")

addition = r'''


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
    ) as static_fallback:
        result = await scraper._official_fallback_with_browser("00405A", object())

    assert result is static_result
    to_thread.assert_awaited_once_with(static_fallback, "00405A")
    static_fallback.assert_not_called()


@pytest.mark.asyncio
async def test_page_creation_exception_isolated_without_attempting_missing_page_cleanup():
    browser_stack = _FakeBrowserStack()
    original_new_page = browser_stack._new_page
    creation_attempts = 0
    recorded = []
    etfs = [{"code": "ETF1"}, {"code": "ETF2"}, {"code": "ETF3"}]

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
    etfs = [{"code": "ETF1"}, {"code": "ETF2"}, {"code": "ETF3"}]

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
    assert recorded[1][1] is False
    assert "scrape failed first" in recorded[1][2]
    assert "page close exploded" in recorded[1][2]
    for page in browser_stack.pages:
        page.close.assert_awaited_once_with()
'''

TARGET.write_text(text.rstrip() + addition + "\n", encoding="utf-8")
