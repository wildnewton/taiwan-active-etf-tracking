import scraper
from scrapers import moneydj


def test_weight_threshold_constants_use_warning_names_only():
    assert moneydj.WARNING_MIN_TOTAL_WEIGHT == 70.0
    assert moneydj.WARNING_MAX_TOTAL_WEIGHT == 140.0
    assert not hasattr(moneydj, "REQUIRED_MIN_TOTAL_WEIGHT")
    assert not hasattr(moneydj, "REQUIRED_MAX_TOTAL_WEIGHT")
    assert not hasattr(moneydj, "PREFERRED_MIN_TOTAL_WEIGHT")
    assert not hasattr(moneydj, "PREFERRED_MAX_TOTAL_WEIGHT")


def test_weight_warning_names_the_raw_source_total_explicitly():
    warning = moneydj._weight_warning(61.98)

    assert warning == {
        "reason": "total_weight_below_expected_range",
        "source_total_weight_all_rows": 61.98,
        "minimum_expected_weight": 70.0,
        "maximum_expected_weight": 140.0,
    }
    assert "total_weight_all_rows" not in warning


def test_min_weight_gate_keeps_raw_source_total_distinct_from_retained_total():
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
    kept_stock = {
        **tiny_stock,
        "asset_name": "保留股票(1001.TW)",
        "stock_code": "1001",
        "weight_pct": 0.01,
    }
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
        "weight_warning": {
            "reason": "total_weight_below_expected_range",
            "source_total_weight_all_rows": 0.013,
            "minimum_expected_weight": 70.0,
            "maximum_expected_weight": 140.0,
        },
    }

    filtered = scraper._apply_min_weight_gate(result)

    assert filtered["total_weight_all_rows"] == 0.01
    assert filtered["weight_warning"]["source_total_weight_all_rows"] == 0.013
