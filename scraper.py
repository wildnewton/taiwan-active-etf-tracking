"""Unified scraper — decision tree for all data sources.

Priority:
  1. MoneyDJ static (fastest, no browser)
  2. MoneyDJ browser (Playwright fallback)
  3. Official browser-based (Capital API, Nomura stealth, Mega/Uni-President Playwright)
  4. Official static (Fubon, Taishin)
  5. Fail
"""

import asyncio
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


def scrape_holdings(etf_code: str) -> dict:
    """Scrape holdings without browser. Tries MoneyDJ static then official static."""
    moneydj_result = scrape_moneydj(etf_code)
    if moneydj_result["ok"] is True:
        return _with_source_type(moneydj_result, "moneydj_primary")

    official_result = scrape_official_static(etf_code)
    if official_result["ok"] is True:
        return _with_source_type(official_result, "official_fallback")

    return FAILED_RESULT.copy()


def scrape_holdings_with_browser(etf_code: str, page) -> dict:
    """Scrape holdings with browser. Full decision tree:
    MoneyDJ static → MoneyDJ browser → Official browser → Official static → Fail
    """
    # 1. MoneyDJ static (fastest)
    moneydj_result = scrape_moneydj(etf_code)
    if moneydj_result["ok"] is True:
        return _with_source_type(moneydj_result, "moneydj_primary")

    # 2. MoneyDJ browser
    browser_result = _run_async(scrape_moneydj_browser(etf_code, page))
    if browser_result["ok"] is True:
        return _with_source_type(browser_result, "moneydj_browser")

    # 3. Official browser-based (Capital API, Nomura stealth, Mega, Uni-President)
    config = get_etf_config(etf_code)
    if config["official_method"] in ("api", "stealth_api", "playwright"):
        official_browser = _run_async(scrape_official_with_browser(etf_code, page))
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
    """Run an async coroutine, handling both sync and async contexts."""
    if not isawaitable(coro):
        return coro

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)  # type: ignore[arg-type]

    raise RuntimeError(
        "scrape_holdings_with_browser cannot run an async browser scraper "
        "inside an active event loop"
    )
