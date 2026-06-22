from unittest.mock import AsyncMock, patch

from scraper import scrape_holdings, scrape_holdings_with_browser


def make_result(ok=True, source_type="moneydj_primary", reason="ok"):
    return {
        "ok": ok,
        "reason": reason,
        "all_rows": [],
        "stock_rows": [],
        "non_stock_rows": [],
        "source_url": "https://example.test",
        "source_type": source_type,
        "total_weight_all_rows": 0.0,
        "total_weight_stock_rows": 0.0,
    }


def test_scrape_holdings_moneydj_primary():
    moneydj_result = make_result(ok=True, source_type="moneydj_primary")

    with patch("scraper.scrape_moneydj", return_value=moneydj_result) as moneydj, \
        patch("scraper.scrape_official_static") as official:
        result = scrape_holdings("00980A")

    assert result["ok"] is True
    assert result["source_type"] == "moneydj_primary"
    moneydj.assert_called_once_with("00980A")
    official.assert_not_called()


def test_scrape_holdings_moneydj_fails_tries_official():
    moneydj_result = make_result(ok=False, source_type="moneydj_primary", reason="blocked")
    official_result = make_result(ok=True, source_type="official_fallback")

    with patch("scraper.scrape_moneydj", return_value=moneydj_result) as moneydj, \
        patch("scraper.scrape_official_static", return_value=official_result) as official:
        result = scrape_holdings("00980A")

    assert result["ok"] is True
    assert result["source_type"] == "official_fallback"
    moneydj.assert_called_once_with("00980A")
    official.assert_called_once_with("00980A")


def test_scrape_holdings_all_fail():
    moneydj_result = make_result(ok=False, source_type="moneydj_primary", reason="blocked")
    official_result = make_result(ok=False, source_type="official_fallback", reason="empty")

    with patch("scraper.scrape_moneydj", return_value=moneydj_result), \
        patch("scraper.scrape_official_static", return_value=official_result):
        result = scrape_holdings("00980A")

    assert result == {
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


def test_scrape_holdings_returns_correct_source_type():
    page = AsyncMock()
    moneydj_result = make_result(ok=False, source_type="moneydj_primary", reason="blocked")
    browser_result = make_result(ok=True, source_type="moneydj_browser")

    with patch("scraper.scrape_moneydj", return_value=moneydj_result), \
        patch("scraper.scrape_moneydj_browser", new=AsyncMock(return_value=browser_result)) as browser, \
        patch("scraper.scrape_official_static") as official:
        result = scrape_holdings_with_browser("00980A", page)

    assert result["ok"] is True
    assert result["source_type"] == "moneydj_browser"
    browser.assert_awaited_once_with("00980A", page)
    official.assert_not_called()
