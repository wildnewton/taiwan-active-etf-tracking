from unittest.mock import patch

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
        result = scrape_holdings("00980A")

    assert stock_codes(result["all_rows"]) == ["3010"]
    assert stock_codes(result["stock_rows"]) == ["3010"]
    assert result["total_weight_all_rows"] == 0.01
    assert result["total_weight_stock_rows"] == 0.01
    official_static.assert_not_called()
