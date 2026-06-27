"""Unified scraper — decision tree for all data sources.

Priority:
  1. MoneyDJ static (fastest, no browser) — retries up to 3x for transient errors
  2. MoneyDJ browser (Playwright fallback)
  3. Official browser-based (Capital API, Nomura stealth, Mega/Uni-President Playwright)
  4. Official static (Fubon, Taishin)
  5. Fail
"""

import asyncio
import time
from inspect import isawaitable

from config import get_etf_config
from scrapers.moneydj import scrape_moneydj
from scrapers.moneydj_browser import scrape_moneydj_browser
from scrapers.official import scrape_official_static, scrape_official_with_browser


FAILED_RESULT = {
    "ok": False,
    "reason": "all sources failed",
    "all_rows": [],
    "stock_rows": [],
    "non_stock_rows": [],
    "source_url": "",
    "source_type": "",
    "total_weight_all_rows": 0.0,
    "total_weight_stock_rows": 0.0,
}

_MONEYDJ_RETRIES = 10
_MONEYDJ_RETRY_DELAYS = []  # Fibonacci * 2: 2, 2, 4, 6, 10, 16, 26, 42, 68


def _build_retry_delays(max_attempts: int) -> list[float]:
    """Generate Fibonacci * 2 delays for max_attempts - 1 gaps.

    Sequence: fib(1)*2, fib(2)*2, fib(3)*2, ... = 2, 2, 4, 6, 10, 16, ...
    """
    delays = []
    a, b = 1, 1  # fib(1), fib(2)
    for _ in range(max_attempts - 1):
        delays.append(a * 2)
        a, b = b, a + b
    return delays


_MONEYDJ_RETRY_DELAYS = _build_retry_delays(_MONEYDJ_RETRIES)


def _retry_moneydj(etf_code: str) -> dict:
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


def scrape_holdings(etf_code: str) -> dict:
    """Scrape holdings without browser. Tries MoneyDJ static then official static."""
    moneydj_result = _retry_moneydj(etf_code)
    if moneydj_result["ok"] is True:
        return _with_source_type(moneydj_result, "moneydj_primary")

    official_result = scrape_official_static(etf_code)
    if official_result["ok"] is True:
        return _with_source_type(official_result, "official_fallback")

    return FAILED_RESULT.copy()


def scrape_holdings_with_browser(etf_code: str, page) -> dict:
    """Sync wrapper for the full browser decision tree.

    Use this from synchronous code when no event loop is running. Async callers
    should call scrape_holdings_with_browser_async directly.
    """
    return _run_async(scrape_holdings_with_browser_async(etf_code, page))


async def scrape_holdings_with_browser_async(etf_code: str, page) -> dict:
    """Async browser-enabled full decision tree.

    MoneyDJ static → MoneyDJ browser → Official browser → Official static → Fail.
    This is the production-safe path for an async Playwright pipeline because it
    avoids nesting asyncio.run inside an already-running event loop.
    """
    # 1. MoneyDJ static (fastest) — retries up to 3x for transient errors
    moneydj_result = _retry_moneydj(etf_code)
    if moneydj_result["ok"] is True:
        return _with_source_type(moneydj_result, "moneydj_primary")

    # 2. MoneyDJ browser
    browser_result = await scrape_moneydj_browser(etf_code, page)
    if browser_result["ok"] is True:
        return _with_source_type(browser_result, "moneydj_browser")

    # 3. Official browser-based (Capital API, Nomura stealth, Mega, Uni-President)
    config = get_etf_config(etf_code)
    if config["official_method"] in ("api", "stealth_api", "playwright"):
        official_browser = await scrape_official_with_browser(etf_code, page)
        if official_browser["ok"] is True:
            return _with_source_type(official_browser, "official_fallback")

    # 4. Official static (Fubon, Taishin)
    official_result = scrape_official_static(etf_code)
    if official_result["ok"] is True:
        return _with_source_type(official_result, "official_fallback")

    return FAILED_RESULT.copy()


def _with_source_type(result: dict, source_type: str) -> dict:
    return {**result, "source_type": source_type}


def _run_async(coro) -> dict:
    """Run an async coroutine from sync code.

    This helper intentionally refuses to run inside an active event loop. In that
    case callers must use the native async API instead of nesting event loops.
    """
    if not isawaitable(coro):
        return coro

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)  # type: ignore[arg-type]

    raise RuntimeError(
        "scrape_holdings_with_browser cannot run an async browser scraper "
        "inside an active event loop; use scrape_holdings_with_browser_async instead"
    )
