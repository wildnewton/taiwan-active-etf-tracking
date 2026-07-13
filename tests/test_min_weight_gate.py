from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

import scraper
from scraper import scrape_holdings


def make_row(asset_name, asset_type, weight_pct, stock_code=None):
    return {
        "date": "2026/07/10",
        "etf_code": "00980A",
        "asset_name": asset_name,
        "asset_type": asset_type,
        "stock_code": stock_code,
        "stock_name": asset_name if stock_code else None,
        "shares": 1000 if stock_code else None,
        "weight_pct": weight_pct,
        "source_url": "https://example.test",
        "source_type": "moneydj_primary",
        "extraction_method": "test",
    }


def make_result(rows, stock_rows=None, non_stock_rows=None):
    if stock_rows is None:
        stock_rows = [row for row in rows if row["asset_type"] == "stock"]
    if non_stock_rows is None:
        non_stock_rows = [row for row in rows if row["asset_type"] != "stock"]
    return {
        "ok": True,
        "reason": "ok",
        "all_rows": rows,
        "stock_rows": stock_rows,
        "non_stock_rows": non_stock_rows,
        "source_url": "https://example.test",
        "source_type": "moneydj_primary",
        "total_weight_all_rows": sum(row["weight_pct"] or 0.0 for row in rows),
        "total_weight_stock_rows": sum(row["weight_pct"] or 0.0 for row in stock_rows),
    }


def make_failed_result():
    return {
        "ok": False,
        "reason": "test failure",
        "all_rows": [],
        "stock_rows": [],
        "non_stock_rows": [],
        "source_url": "",
        "source_type": "",
        "total_weight_all_rows": 0.0,
        "total_weight_stock_rows": 0.0,
    }


def stock_codes(rows):
    return [row["stock_code"] for row in rows if row["asset_type"] == "stock"]


def test_min_weight_gate_filters_out_stocks_below_threshold():
    rows = [
        make_row("零權重", "stock", 0.0, "1000"),
        make_row("四厘", "stock", 0.004, "1004"),
        make_row("九厘", "stock", 0.009, "1009"),
        make_row("一分", "stock", 0.01, "1010"),
        make_row("大權重", "stock", 1.23, "1230"),
    ]

    result = scraper._apply_min_weight_gate(make_result(rows))

    assert stock_codes(result["all_rows"]) == ["1010", "1230"]
    assert stock_codes(result["stock_rows"]) == ["1010", "1230"]
    assert result["total_weight_all_rows"] == 1.24
    assert result["total_weight_stock_rows"] == 1.24


def test_min_weight_gate_preserves_non_stock_assets():
    futures = make_row("臺股期貨", "futures", -0.25)
    options = make_row("臺指選擇權", "options", 0.0)
    stock = make_row("零權重股票", "stock", 0.0, "2000")
    result = scraper._apply_min_weight_gate(make_result([futures, options, stock]))

    assert result["all_rows"] == [futures, options]
    assert result["non_stock_rows"] == [futures, options]
    assert result["stock_rows"] == []
    assert result["total_weight_all_rows"] == -0.25
    assert result["total_weight_stock_rows"] == 0.0


def test_min_weight_gate_applied_in_sync_scrape_path():
    zero_weight = make_row("零權重股票", "stock", 0.0, "3000")
    kept_stock = make_row("保留股票", "stock", 0.01, "3010")
    moneydj_result = make_result([zero_weight, kept_stock])

    with patch("scraper.scrape_moneydj", return_value=moneydj_result), \
        patch("scraper._is_stale_result", return_value=False), \
        patch("scraper.get_historical_mean_stock_row_count", return_value=None), \
        patch("scraper.scrape_official_static") as official_static, \
        patch("time.sleep"):
        result = scrape_holdings("00980A", target_date=date(2026, 7, 10))

    assert stock_codes(result["all_rows"]) == ["3010"]
    assert stock_codes(result["stock_rows"]) == ["3010"]
    assert result["total_weight_all_rows"] == 0.01
    assert result["total_weight_stock_rows"] == 0.01
    official_static.assert_not_called()


def test_row_count_validation_uses_post_filter_stock_count():
    tiny_rows = [
        make_row(f"微量{i}", "stock", 0.009, f"10{i:02d}")
        for i in range(6)
    ]
    kept_moneydj_rows = [
        make_row(f"MoneyDJ保留{i}", "stock", 0.01, f"20{i:02d}")
        for i in range(4)
    ]
    official_rows = [
        make_row(f"官方保留{i}", "stock", 0.02, f"30{i:02d}")
        for i in range(7)
    ]
    moneydj_result = make_result(tiny_rows + kept_moneydj_rows)
    official_result = make_result(official_rows)

    with patch("scraper.scrape_moneydj", return_value=moneydj_result), \
        patch("scraper._is_stale_result", return_value=False), \
        patch("scraper.get_historical_mean_stock_row_count", return_value=10), \
        patch("scraper.scrape_official_static", return_value=official_result) as official_static, \
        patch("time.sleep"):
        result = scrape_holdings("00980A", target_date=date(2026, 7, 10))

    official_static.assert_called_once_with("00980A")
    assert result["source_type"] == "official_fallback"
    assert stock_codes(result["stock_rows"]) == [f"30{i:02d}" for i in range(7)]
    assert "row_count_warning" not in result


def test_min_weight_gate_applied_to_sync_official_fallback():
    zero_weight = make_row("官方零權重", "stock", 0.0, "4000")
    kept_stock = make_row("官方保留", "stock", 0.01, "4010")
    official_result = make_result([zero_weight, kept_stock])

    with patch("scraper.scrape_moneydj", return_value=make_failed_result()), \
        patch("scraper.scrape_official_static", return_value=official_result), \
        patch("time.sleep"):
        result = scrape_holdings("00980A", target_date=date(2026, 7, 10))

    assert result["source_type"] == "official_fallback"
    assert stock_codes(result["all_rows"]) == ["4010"]
    assert stock_codes(result["stock_rows"]) == ["4010"]
    assert result["total_weight_all_rows"] == 0.01
    assert result["total_weight_stock_rows"] == 0.01


@pytest.mark.asyncio
async def test_min_weight_gate_applied_to_async_browser_path():
    zero_weight = make_row("瀏覽器零權重", "stock", 0.0, "5000")
    kept_stock = make_row("瀏覽器保留", "stock", 0.01, "5010")
    browser_result = make_result([zero_weight, kept_stock])
    browser_result["source_type"] = "moneydj_browser"
    official_fallback = AsyncMock()

    with patch("scraper._retry_moneydj", return_value=make_failed_result()), \
        patch("scraper.scrape_moneydj_browser", new=AsyncMock(return_value=browser_result)), \
        patch("scraper._is_stale_result", return_value=False), \
        patch("scraper.get_historical_mean_stock_row_count", return_value=None), \
        patch("scraper._official_fallback_with_browser", new=official_fallback):
        result = await scraper.scrape_holdings_with_browser_async(
            "00980A",
            object(),
            target_date=date(2026, 7, 10),
        )

    assert result["source_type"] == "moneydj_browser"
    assert stock_codes(result["all_rows"]) == ["5010"]
    assert stock_codes(result["stock_rows"]) == ["5010"]
    assert result["total_weight_all_rows"] == 0.01
    assert result["total_weight_stock_rows"] == 0.01
    official_fallback.assert_not_awaited()
