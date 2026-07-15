from pathlib import Path


SCRAPER = Path("scripts/scraper.py")
PIPELINE = Path("scripts/pipeline.py")
SCRAPER_TEST = Path("tests/test_scraper.py")
PREEXISTING_TEST = Path("tests/test_preexisting_successful_snapshots.py")
FRESHNESS_TEST = Path("tests/test_scraper_freshness_target.py")
CUTOFF_TEST = Path("tests/test_expected_data_date_cutoff.py")
MIN_WEIGHT_TEST = Path("tests/test_min_weight_gate.py")
WEIGHT_WARNING_TEST = Path("tests/test_weight_validation_warnings.py")


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one match in {path}, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_scraper() -> None:
    replace_once(
        SCRAPER,
        '''def _retry_moneydj(etf_code: str) -> dict:
    """Call scrape_moneydj with up to _MONEYDJ_RETRIES attempts.

    Returns the first successful result, or the last failure result if all fail.
    Sleeps between attempts using Fibonacci * 2 backoff.
    """
    last_result = FAILED_RESULT.copy()
    for attempt in range(_MONEYDJ_RETRIES):
        last_result = scrape_moneydj(etf_code)
        if last_result["ok"] is True:
            return last_result
        if attempt < _MONEYDJ_RETRIES - 1:
            time.sleep(_MONEYDJ_RETRY_DELAYS[attempt])
    return last_result


''',
        '''def _retry_moneydj(etf_code: str) -> dict:
    """Call scrape_moneydj synchronously with Fibonacci backoff."""
    last_result = FAILED_RESULT.copy()
    for attempt in range(_MONEYDJ_RETRIES):
        last_result = scrape_moneydj(etf_code)
        if last_result["ok"] is True:
            return last_result
        if attempt < _MONEYDJ_RETRIES - 1:
            time.sleep(_MONEYDJ_RETRY_DELAYS[attempt])
    return last_result


async def _retry_moneydj_async(etf_code: str) -> dict:
    """Run MoneyDJ attempts off the event loop with async backoff."""
    last_result = FAILED_RESULT.copy()
    for attempt in range(_MONEYDJ_RETRIES):
        last_result = await asyncio.to_thread(scrape_moneydj, etf_code)
        if last_result["ok"] is True:
            return last_result
        if attempt < _MONEYDJ_RETRIES - 1:
            await asyncio.sleep(_MONEYDJ_RETRY_DELAYS[attempt])
    return last_result


''',
    )
    replace_once(
        SCRAPER,
        '''    target_date = _require_target_date(target_date)
    # 1. MoneyDJ static (fastest) — retries up to 3x for transient errors
    moneydj_result = _retry_moneydj(etf_code)
''',
        '''    target_date = _require_target_date(target_date)
    # 1. MoneyDJ static (fastest) — synchronous request work runs off-loop.
    moneydj_result = await _retry_moneydj_async(etf_code)
''',
    )


def patch_pipeline() -> None:
    replace_once(
        PIPELINE,
        'from scraper import scrape_holdings, scrape_holdings_with_browser_async\n',
        '''from scraper import (
    FAILED_RESULT,
    scrape_holdings,
    scrape_holdings_with_browser_async,
)
''',
    )
    replace_once(
        PIPELINE,
        '''TAIPEI_TIMEZONE = ZoneInfo("Asia/Taipei")
DATA_AVAILABILITY_CUTOFF = time(15, 0)
''',
        '''TAIPEI_TIMEZONE = ZoneInfo("Asia/Taipei")
DATA_AVAILABILITY_CUTOFF = time(15, 0)
_ASYNC_SCRAPE_CONCURRENCY = 3
''',
    )
    replace_once(
        PIPELINE,
        '''    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            context = await browser.new_context(locale="zh-TW")
            try:
                browser_page = await context.new_page()
                return await _execute_scrape_async(
                    etfs_to_scrape,
                    _browser_scrape_fn(browser_page),
                    run_date,
                    expected_data_date,
                    summary,
                )
            finally:
                await context.close()
        finally:
            await browser.close()
''',
        '''    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            context = await browser.new_context(locale="zh-TW")
            try:
                return await _execute_scrape_async_with_pages(
                    etfs_to_scrape,
                    context,
                    run_date,
                    expected_data_date,
                    summary,
                )
            finally:
                await context.close()
        finally:
            await browser.close()
''',
    )
    replace_once(
        PIPELINE,
        '''async def _execute_scrape_async(
    etfs: list[dict],
    scrape_fn: AsyncScrapeFn,
    run_date: date,
    expected_data_date: Optional[date],
    summary: dict,
) -> dict:
    freshness_target_date = expected_data_date or run_date
    for etf in etfs:
        etf_code = etf["code"]
        started_at = datetime.now()
        result = await scrape_fn(etf_code, freshness_target_date)
        finished_at = datetime.now()
        _record_result(
            summary,
            etf_code,
            run_date,
            expected_data_date,
            started_at,
            finished_at,
            result,
        )

    _finalize_data_date_range(summary)
    return summary


''',
        '''async def _execute_scrape_async(
    etfs: list[dict],
    scrape_fn: AsyncScrapeFn,
    run_date: date,
    expected_data_date: Optional[date],
    summary: dict,
) -> dict:
    freshness_target_date = expected_data_date or run_date
    for etf in etfs:
        etf_code = etf["code"]
        started_at = datetime.now()
        result = await scrape_fn(etf_code, freshness_target_date)
        finished_at = datetime.now()
        _record_result(
            summary,
            etf_code,
            run_date,
            expected_data_date,
            started_at,
            finished_at,
            result,
        )

    _finalize_data_date_range(summary)
    return summary


async def _execute_scrape_async_with_pages(
    etfs: list[dict],
    context,
    run_date: date,
    expected_data_date: Optional[date],
    summary: dict,
) -> dict:
    """Scrape concurrently, then record sequentially in ETF input order."""
    freshness_target_date = expected_data_date or run_date
    semaphore = asyncio.Semaphore(_ASYNC_SCRAPE_CONCURRENCY)

    async def scrape_one(etf: dict):
        etf_code = etf["code"]
        async with semaphore:
            started_at = datetime.now()
            page = None
            try:
                page = await context.new_page()
                result = await scrape_holdings_with_browser_async(
                    etf_code,
                    page,
                    target_date=freshness_target_date,
                )
            except Exception as exc:
                result = {
                    **FAILED_RESULT,
                    "reason": f"unhandled scraper exception: {exc}",
                }
            finally:
                if page is not None:
                    await page.close()
            finished_at = datetime.now()
            return etf_code, started_at, finished_at, result

    outcomes = await asyncio.gather(*(scrape_one(etf) for etf in etfs))
    for etf_code, started_at, finished_at, result in outcomes:
        _record_result(
            summary,
            etf_code,
            run_date,
            expected_data_date,
            started_at,
            finished_at,
            result,
        )

    _finalize_data_date_range(summary)
    return summary


''',
    )


def patch_tests() -> None:
    replace_once(
        SCRAPER_TEST,
        '''        patch("scraper.scrape_official_static") as official_static, \\
        patch("time.sleep") as sleep:
        result = scrape_holdings_with_browser("00980A", page, target_date=FixedDate.today())

    assert result["ok"] is True
    assert result["source_type"] == "moneydj_primary"
    assert moneydj.call_count == 3
    browser.assert_not_called()
    official_browser.assert_not_called()
    official_static.assert_not_called()
    assert sleep.call_count == 2
''',
        '''        patch("scraper.scrape_official_static") as official_static, \\
        patch("scraper.asyncio.sleep", new=AsyncMock()) as sleep:
        result = scrape_holdings_with_browser("00980A", page, target_date=FixedDate.today())

    assert result["ok"] is True
    assert result["source_type"] == "moneydj_primary"
    assert moneydj.call_count == 3
    browser.assert_not_called()
    official_browser.assert_not_called()
    official_static.assert_not_called()
    assert [item.args[0] for item in sleep.await_args_list] == [2, 2]
''',
    )
    replace_once(
        SCRAPER_TEST,
        '''        patch("scraper.scrape_official_static") as official_static, \\
        patch("time.sleep") as sleep:
        result = scrape_holdings_with_browser("00980A", page, target_date=FixedDate.today())

    assert result["ok"] is True
    assert result["source_type"] == "moneydj_browser"
    assert moneydj.call_count == 10
    browser.assert_awaited_once_with("00980A", page)
    official_browser.assert_not_called()
    official_static.assert_not_called()
    assert sleep.call_count == 9
''',
        '''        patch("scraper.scrape_official_static") as official_static, \\
        patch("scraper.asyncio.sleep", new=AsyncMock()) as sleep:
        result = scrape_holdings_with_browser("00980A", page, target_date=FixedDate.today())

    assert result["ok"] is True
    assert result["source_type"] == "moneydj_browser"
    assert moneydj.call_count == 10
    browser.assert_awaited_once_with("00980A", page)
    official_browser.assert_not_called()
    official_static.assert_not_called()
    assert [item.args[0] for item in sleep.await_args_list] == scraper._MONEYDJ_RETRY_DELAYS
''',
    )
    replace_once(
        PREEXISTING_TEST,
        '''        self.page = object()
        self.context = Mock()
''',
        '''        self.page = Mock()
        self.page.close = AsyncMock()
        self.context = Mock()
''',
    )
    replace_once(
        FRESHNESS_TEST,
        '''    with patch("scraper._retry_moneydj", return_value=moneydj), \\
        patch("scraper._official_fallback_with_browser", new=official_fallback), \\
''',
        '''    with patch("scraper._retry_moneydj_async", new=AsyncMock(return_value=moneydj)), \\
        patch("scraper._official_fallback_with_browser", new=official_fallback), \\
''',
    )
    replace_once(
        CUTOFF_TEST,
        '    with patch("scraper._retry_moneydj", new=retry_moneydj):\n',
        '    with patch("scraper._retry_moneydj_async", new=retry_moneydj):\n',
    )
    replace_once(
        MIN_WEIGHT_TEST,
        '''    with patch("scraper._retry_moneydj", return_value=make_failed_result()), \\
        patch("scraper.scrape_moneydj_browser", new=AsyncMock(return_value=browser_result)), \\
''',
        '''    with patch("scraper._retry_moneydj_async", new=AsyncMock(return_value=make_failed_result())), \\
        patch("scraper.scrape_moneydj_browser", new=AsyncMock(return_value=browser_result)), \\
''',
    )
    replace_once(
        WEIGHT_WARNING_TEST,
        '''    with patch("scraper._retry_moneydj", return_value=warned), patch(
        "scraper.scrape_moneydj_browser", new=moneydj_browser
''',
        '''    with patch("scraper._retry_moneydj_async", new=AsyncMock(return_value=warned)), patch(
        "scraper.scrape_moneydj_browser", new=moneydj_browser
''',
    )


def main() -> None:
    patch_scraper()
    patch_pipeline()
    patch_tests()


if __name__ == "__main__":
    main()
