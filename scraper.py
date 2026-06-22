import asyncio
from inspect import isawaitable

from scrapers.moneydj import scrape_moneydj
from scrapers.moneydj_browser import scrape_moneydj_browser
from scrapers.official import scrape_official_static


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
    moneydj_result = scrape_moneydj(etf_code)
    if moneydj_result["ok"] is True:
        return _with_source_type(moneydj_result, "moneydj_primary")

    official_result = scrape_official_static(etf_code)
    if official_result["ok"] is True:
        return _with_source_type(official_result, "official_fallback")

    return FAILED_RESULT.copy()


def scrape_holdings_with_browser(etf_code: str, page) -> dict:
    moneydj_result = scrape_moneydj(etf_code)
    if moneydj_result["ok"] is True:
        return _with_source_type(moneydj_result, "moneydj_primary")

    browser_result = _run_browser_scraper(etf_code, page)
    if browser_result["ok"] is True:
        return _with_source_type(browser_result, "moneydj_browser")

    official_result = scrape_official_static(etf_code)
    if official_result["ok"] is True:
        return _with_source_type(official_result, "official_fallback")

    return FAILED_RESULT.copy()


def _with_source_type(result: dict, source_type: str) -> dict:
    return {**result, "source_type": source_type}


def _run_browser_scraper(etf_code: str, page) -> dict:
    result = scrape_moneydj_browser(etf_code, page)
    if not isawaitable(result):
        return result

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(result)

    raise RuntimeError(
        "scrape_holdings_with_browser cannot run an async browser scraper "
        "inside an active event loop"
    )
