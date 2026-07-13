from datetime import date
from unittest.mock import AsyncMock, patch

from scraper import scrape_holdings, scrape_holdings_with_browser, _MONEYDJ_RETRY_DELAYS


class FixedDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 7, 7)


def make_result(ok=True, source_type="moneydj_primary", reason="ok", row_date=None):
    rows = []
    if row_date is not None:
        rows = [
            {
                "date": row_date,
                "etf_code": "00980A",
                "asset_name": "台積電(2330.TW)",
                "asset_type": "stock",
                "stock_code": "2330",
                "stock_name": "台積電",
                "shares": 1000,
                "weight_pct": 10.0,
                "source_url": "https://example.test",
                "source_type": source_type,
                "extraction_method": "requests_bs4",
            }
        ]
    return {
        "ok": ok,
        "reason": reason,
        "all_rows": rows,
        "stock_rows": rows,
        "non_stock_rows": [],
        "source_url": "https://example.test",
        "source_type": source_type,
        "total_weight_all_rows": 10.0 if ok else 0.0,
        "total_weight_stock_rows": 10.0 if ok else 0.0,
    }


def test_retry_delays_fibonacci_times_two():
    """_MONEYDJ_RETRY_DELAYS follows Fibonacci * 2 pattern: 2,2,4,6,10,16,26,42,68."""
    assert len(_MONEYDJ_RETRY_DELAYS) == 9  # 10 attempts = 9 gaps
    assert _MONEYDJ_RETRY_DELAYS == [2, 2, 4, 6, 10, 16, 26, 42, 68]


def test_scrape_holdings_moneydj_primary():
    """MoneyDJ succeeds first try → returns immediately, no retry."""
    moneydj_result = make_result(ok=True, source_type="moneydj_primary")

    with patch("scraper.scrape_moneydj", return_value=moneydj_result) as moneydj, \
        patch("scraper.scrape_official_static") as official, \
        patch("time.sleep") as sleep:
        result = scrape_holdings("00980A", target_date=FixedDate.today())

    assert result["ok"] is True
    assert result["source_type"] == "moneydj_primary"
    moneydj.assert_called_once_with("00980A")
    official.assert_not_called()
    sleep.assert_not_called()


def test_scrape_holdings_moneydj_stale_uses_fresh_official():
    stale_moneydj = make_result(ok=True, source_type="moneydj_primary", row_date="2026/07/06")
    fresh_official = make_result(ok=True, source_type="official_fallback", row_date="2026/07/07")

    with patch("scraper.date", FixedDate), \
        patch("scraper.scrape_moneydj", return_value=stale_moneydj), \
        patch("scraper.scrape_official_static", return_value=fresh_official) as official, \
        patch("time.sleep"):
        result = scrape_holdings("00980A", target_date=FixedDate.today())

    assert result["ok"] is True
    assert result["source_type"] == "official_fallback"
    assert result["stock_rows"][0]["date"] == "2026/07/07"
    official.assert_called_once_with("00980A")


def test_scrape_holdings_moneydj_stale_keeps_moneydj_when_official_not_fresh():
    stale_moneydj = make_result(ok=True, source_type="moneydj_primary", row_date="2026/07/06")
    stale_official = make_result(ok=True, source_type="official_fallback", row_date="2026/07/06")

    with patch("scraper.date", FixedDate), \
        patch("scraper.scrape_moneydj", return_value=stale_moneydj), \
        patch("scraper.scrape_official_static", return_value=stale_official), \
        patch("time.sleep"):
        result = scrape_holdings("00980A", target_date=FixedDate.today())

    assert result["ok"] is True
    assert result["source_type"] == "moneydj_primary"
    assert result["stock_rows"][0]["date"] == "2026/07/06"


def test_scrape_holdings_retry_then_succeeds():
    """MoneyDJ fails twice → succeeds on 3rd call. No official needed."""
    fail = make_result(ok=False, source_type="moneydj_primary", reason="timeout")
    success = make_result(ok=True, source_type="moneydj_primary")

    with patch("scraper.scrape_moneydj", side_effect=[fail, fail, success]) as moneydj, \
        patch("scraper.scrape_official_static") as official, \
        patch("time.sleep") as sleep:
        result = scrape_holdings("00980A", target_date=FixedDate.today())

    assert result["ok"] is True
    assert result["source_type"] == "moneydj_primary"
    assert moneydj.call_count == 3
    official.assert_not_called()
    assert sleep.call_count == 2


def test_scrape_holdings_retry_all_fail_goes_to_official():
    """MoneyDJ retries 10x then all fail → falls to official success."""
    fail = make_result(ok=False, source_type="moneydj_primary", reason="timeout")
    official_result = make_result(ok=True, source_type="official_fallback")

    with patch("scraper.scrape_moneydj", side_effect=[fail] * 10) as moneydj, \
        patch("scraper.scrape_official_static", return_value=official_result) as official, \
        patch("time.sleep") as sleep:
        result = scrape_holdings("00980A", target_date=FixedDate.today())

    assert result["ok"] is True
    assert result["source_type"] == "official_fallback"
    assert moneydj.call_count == 10
    official.assert_called_once_with("00980A")
    assert sleep.call_count == 9


def test_scrape_holdings_retry_all_fail_goes_to_failed():
    """MoneyDJ and official both fail after retries → FAILED_RESULT."""
    fail = make_result(ok=False, source_type="moneydj_primary", reason="timeout")
    official_result = make_result(ok=False, source_type="official_fallback", reason="empty")

    with patch("scraper.scrape_moneydj", side_effect=[fail] * 10), \
        patch("scraper.scrape_official_static", return_value=official_result), \
        patch("time.sleep"):
        result = scrape_holdings("00980A", target_date=FixedDate.today())

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


# ── Browser path retry tests ──
# scrape_holdings_with_browser is a sync wrapper around the async function.
# _run_async will run the real async function; we mock the scrapers it calls.

def test_scrape_with_browser_retry_then_moneydj_primary():
    """Browser path: MoneyDJ retry then succeeds → moneydj_primary."""
    page = AsyncMock()
    fail = make_result(ok=False, source_type="moneydj_primary", reason="timeout")
    success = make_result(ok=True, source_type="moneydj_primary")

    with patch("scraper.scrape_moneydj", side_effect=[fail, fail, success]) as moneydj, \
        patch("scraper.scrape_moneydj_browser", new=AsyncMock()) as browser, \
        patch("scraper.scrape_official_with_browser", new=AsyncMock()) as official_browser, \
        patch("scraper.scrape_official_static") as official_static, \
        patch("time.sleep") as sleep:
        result = scrape_holdings_with_browser("00980A", page, target_date=FixedDate.today())

    assert result["ok"] is True
    assert result["source_type"] == "moneydj_primary"
    assert moneydj.call_count == 3
    browser.assert_not_called()
    official_browser.assert_not_called()
    official_static.assert_not_called()
    assert sleep.call_count == 2


def test_scrape_with_browser_stale_moneydj_uses_fresh_official_browser():
    page = AsyncMock()
    stale_moneydj = make_result(ok=True, source_type="moneydj_primary", row_date="2026/07/06")
    fresh_official = make_result(ok=True, source_type="official_fallback", row_date="2026/07/07")

    with patch("scraper.date", FixedDate), \
        patch("scraper.get_etf_config", return_value={"official_method": "api"}), \
        patch("scraper.scrape_moneydj", return_value=stale_moneydj) as moneydj, \
        patch("scraper.scrape_moneydj_browser", new=AsyncMock()) as browser, \
        patch("scraper.scrape_official_with_browser", new=AsyncMock(return_value=fresh_official)) as official_browser, \
        patch("scraper.scrape_official_static") as official_static, \
        patch("time.sleep"):
        result = scrape_holdings_with_browser(
            "00980A",
            page,
            target_date=FixedDate.today(),
        )

    assert result["ok"] is True
    assert result["source_type"] == "official_fallback"
    assert result["stock_rows"][0]["date"] == "2026/07/07"
    moneydj.assert_called_once_with("00980A")
    browser.assert_not_called()
    official_browser.assert_awaited_once_with("00980A", page)
    official_static.assert_not_called()


def test_scrape_with_browser_retry_all_fail_then_browser():
    """Browser path: MoneyDJ all 10 retries fail → browser fallback succeeds."""
    page = AsyncMock()
    fail = make_result(ok=False, source_type="moneydj_primary", reason="timeout")
    browser_result = make_result(ok=True, source_type="moneydj_browser")

    with patch("scraper.scrape_moneydj", side_effect=[fail] * 10) as moneydj, \
        patch("scraper.scrape_moneydj_browser", new=AsyncMock(return_value=browser_result)) as browser, \
        patch("scraper.scrape_official_with_browser", new=AsyncMock()) as official_browser, \
        patch("scraper.scrape_official_static") as official_static, \
        patch("time.sleep") as sleep:
        result = scrape_holdings_with_browser("00980A", page, target_date=FixedDate.today())

    assert result["ok"] is True
    assert result["source_type"] == "moneydj_browser"
    assert moneydj.call_count == 10
    browser.assert_awaited_once_with("00980A", page)
    official_browser.assert_not_called()
    official_static.assert_not_called()
    assert sleep.call_count == 9


def test_scrape_with_browser_first_try_immediate():
    """Browser path: MoneyDJ succeeds first try → no retry, no fallback."""
    page = AsyncMock()
    success = make_result(ok=True, source_type="moneydj_primary")

    with patch("scraper.scrape_moneydj", return_value=success) as moneydj, \
        patch("scraper.scrape_moneydj_browser", new=AsyncMock()) as browser, \
        patch("scraper.scrape_official_with_browser", new=AsyncMock()) as official_browser, \
        patch("scraper.scrape_official_static") as official_static:
        result = scrape_holdings_with_browser("00980A", page, target_date=FixedDate.today())

    assert result["ok"] is True
    assert result["source_type"] == "moneydj_primary"
    moneydj.assert_called_once_with("00980A")
    browser.assert_not_called()
    official_browser.assert_not_called()
    official_static.assert_not_called()
