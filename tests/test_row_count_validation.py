from datetime import date, datetime
from unittest.mock import AsyncMock, patch

import pipeline
from pipeline import run_daily_scrape
from scraper import scrape_holdings, scrape_holdings_with_browser


class FixedDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 7, 8)


def make_stock_rows(etf_code="00984A", count=55, row_date="2026/07/08", source_type="moneydj_primary"):
    return [
        {
            "date": row_date,
            "etf_code": etf_code,
            "asset_name": f"股票{i:04d}({1000 + i}.TW)",
            "asset_type": "stock",
            "stock_code": str(1000 + i),
            "stock_name": f"股票{i:04d}",
            "shares": 1000 + i,
            "weight_pct": 1.0,
            "source_url": "https://example.test",
            "source_type": source_type,
            "extraction_method": "test",
        }
        for i in range(count)
    ]


def make_result(ok=True, etf_code="00984A", count=55, source_type="moneydj_primary", reason="ok", row_date="2026/07/08"):
    rows = make_stock_rows(etf_code=etf_code, count=count, row_date=row_date, source_type=source_type) if ok else []
    return {
        "ok": ok,
        "reason": reason,
        "all_rows": rows,
        "stock_rows": rows,
        "non_stock_rows": [],
        "source_url": "https://example.test" if ok else "",
        "source_type": source_type if ok else "",
        "total_weight_all_rows": float(count) if ok else 0.0,
        "total_weight_stock_rows": float(count) if ok else 0.0,
    }


def test_low_moneydj_row_count_uses_official_when_official_recovers_rows():
    moneydj = make_result(count=55, source_type="moneydj_primary")
    official = make_result(count=122, source_type="official_fallback")

    with patch("scraper.date", FixedDate), \
        patch("scraper.get_historical_mean_stock_row_count", return_value=115.0), \
        patch("scraper.scrape_moneydj", return_value=moneydj), \
        patch("scraper.scrape_official_static", return_value=official) as official_static, \
        patch("time.sleep"):
        result = scrape_holdings("00984A", target_date=FixedDate.today())

    assert result["ok"] is True
    assert result["source_type"] == "official_fallback"
    assert len(result["stock_rows"]) == 122
    assert "row_count_warning" not in result
    official_static.assert_called_once_with("00984A")


def test_low_moneydj_row_count_keeps_moneydj_when_official_rows_are_stale():
    moneydj = make_result(count=55, source_type="moneydj_primary", row_date="2026/07/08")
    stale_official = make_result(count=122, source_type="official_fallback", row_date="2026/07/07")

    with patch("scraper.date", FixedDate), \
        patch("scraper.get_historical_mean_stock_row_count", return_value=115.0), \
        patch("scraper.scrape_moneydj", return_value=moneydj), \
        patch("scraper.scrape_official_static", return_value=stale_official), \
        patch("time.sleep"):
        result = scrape_holdings("00984A", target_date=FixedDate.today())

    assert result["ok"] is True
    assert result["source_type"] == "moneydj_primary"
    assert result["stock_rows"][0]["date"] == "2026/07/08"
    assert result["row_count_warning"]["reason"] == "low_row_count_official_fallback_stale"
    assert result["row_count_warning"]["official_data_date"] == "2026-07-07"
    assert result["row_count_warning"]["moneydj_data_date"] == "2026-07-08"


def test_low_moneydj_row_count_confirmed_by_same_nonzero_official_count_is_manual_inspection():
    moneydj = make_result(count=55, source_type="moneydj_primary")
    official = make_result(count=55, source_type="official_fallback")

    with patch("scraper.date", FixedDate), \
        patch("scraper.get_historical_mean_stock_row_count", return_value=115.0), \
        patch("scraper.scrape_moneydj", return_value=moneydj), \
        patch("scraper.scrape_official_static", return_value=official), \
        patch("time.sleep"):
        result = scrape_holdings("00984A", target_date=FixedDate.today())

    assert result["ok"] is True
    assert result["source_type"] == "moneydj_primary"
    assert len(result["stock_rows"]) == 55
    assert result["manual_inspection_required"] is True
    assert result["row_count_warning"]["reason"] == "low_row_count_confirmed_by_fallback"
    assert result["row_count_warning"]["moneydj_stock_rows"] == 55
    assert result["row_count_warning"]["official_stock_rows"] == 55


def test_low_moneydj_row_count_keeps_moneydj_when_official_fallback_fails():
    moneydj = make_result(count=55, source_type="moneydj_primary")
    official = make_result(ok=False, count=0, reason="official timeout")

    with patch("scraper.date", FixedDate), \
        patch("scraper.get_historical_mean_stock_row_count", return_value=115.0), \
        patch("scraper.scrape_moneydj", return_value=moneydj), \
        patch("scraper.scrape_official_static", return_value=official), \
        patch("time.sleep"):
        result = scrape_holdings("00984A", target_date=FixedDate.today())

    assert result["ok"] is True
    assert result["source_type"] == "moneydj_primary"
    assert result["row_count_warning"]["reason"] == "low_row_count_official_fallback_failed"
    assert result["row_count_warning"]["official_error"] == "official timeout"


def test_moneydj_row_count_validation_skips_when_history_is_missing():
    moneydj = make_result(count=55, source_type="moneydj_primary")

    with patch("scraper.date", FixedDate), \
        patch("scraper.get_historical_mean_stock_row_count", return_value=None), \
        patch("scraper.scrape_moneydj", return_value=moneydj), \
        patch("scraper.scrape_official_static") as official_static, \
        patch("time.sleep"):
        result = scrape_holdings("00984A", target_date=FixedDate.today())

    assert result["ok"] is True
    assert result["source_type"] == "moneydj_primary"
    assert "row_count_warning" not in result
    official_static.assert_not_called()


def test_low_moneydj_row_count_uses_browser_official_fallback_when_available():
    page = AsyncMock()
    moneydj = make_result(count=55, source_type="moneydj_primary")
    official = make_result(count=122, source_type="official_fallback")

    with patch("scraper.date", FixedDate), \
        patch("scraper.get_historical_mean_stock_row_count", return_value=115.0), \
        patch("scraper.get_etf_config", return_value={"official_method": "api"}), \
        patch("scraper.scrape_moneydj", return_value=moneydj), \
        patch("scraper.scrape_moneydj_browser", new=AsyncMock()) as browser, \
        patch("scraper.scrape_official_with_browser", new=AsyncMock(return_value=official)) as official_browser, \
        patch("scraper.scrape_official_static") as official_static, \
        patch("time.sleep"):
        result = scrape_holdings_with_browser("00984A", page, target_date=FixedDate.today())

    assert result["ok"] is True
    assert result["source_type"] == "official_fallback"
    assert len(result["stock_rows"]) == 122
    browser.assert_not_called()
    official_browser.assert_awaited_once_with("00984A", page)
    official_static.assert_not_called()


def test_pipeline_summary_surfaces_row_count_manual_inspection_warnings():
    warning = {
        "reason": "low_row_count_confirmed_by_fallback",
        "manual_inspection_required": True,
        "moneydj_stock_rows": 55,
        "official_stock_rows": 55,
        "historical_mean_stock_rows": 115.0,
        "minimum_expected_stock_rows": 69.0,
    }
    result = {
        **make_result(count=55, source_type="moneydj_primary"),
        "manual_inspection_required": True,
        "row_count_warning": warning,
    }

    with patch("pipeline.date", FixedDate), \
        patch("pipeline._current_run_at", return_value=datetime.combine(
            FixedDate.today(),
            pipeline.DATA_AVAILABILITY_CUTOFF,
            tzinfo=pipeline.TAIPEI_TIMEZONE,
        )), \
        patch("pipeline._active_etfs_for_run", return_value=[{"code": "00984A"}]), \
        patch("pipeline.scrape_holdings", return_value=result), \
        patch("pipeline.init_db"), \
        patch("pipeline.replace_daily_snapshot", return_value={"inserted": True}):
        summary = run_daily_scrape(":memory:")

    assert summary["row_count_warnings"] == [{"etf_code": "00984A", **warning}]
