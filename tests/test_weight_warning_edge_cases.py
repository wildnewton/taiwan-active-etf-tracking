import scraper
from scrapers.moneydj import _weight_warning


def test_weight_warning_threshold_boundaries_are_in_range():
    assert _weight_warning(70.0) is None
    assert _weight_warning(140.0) is None


def test_min_weight_gate_preserves_raw_weight_warning():
    warning = {
        "reason": "total_weight_below_expected_range",
        "total_weight_all_rows": 0.013,
        "minimum_expected_weight": 70.0,
        "maximum_expected_weight": 140.0,
    }
    tiny_stock = {
        "date": "2026/07/15",
        "etf_code": "00406A",
        "asset_name": "微量股票(1000.TW)",
        "asset_type": "stock",
        "stock_code": "1000",
        "stock_name": "微量股票",
        "shares": 1000,
        "weight_pct": 0.003,
        "source_url": "https://example.test",
        "source_type": "moneydj_primary",
        "extraction_method": "test",
    }
    kept_stock = {**tiny_stock, "asset_name": "保留股票(1001.TW)", "stock_code": "1001", "weight_pct": 0.01}
    result = {
        "ok": True,
        "reason": "ok",
        "all_rows": [tiny_stock, kept_stock],
        "stock_rows": [tiny_stock, kept_stock],
        "non_stock_rows": [],
        "source_url": "https://example.test",
        "source_type": "moneydj_primary",
        "total_weight_all_rows": 0.013,
        "total_weight_stock_rows": 0.013,
        "weight_warning": warning,
    }

    filtered = scraper._apply_min_weight_gate(result)

    assert filtered["stock_rows"] == [kept_stock]
    assert filtered["total_weight_all_rows"] == 0.01
    assert filtered["weight_warning"] == warning
