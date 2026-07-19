from datetime import date, datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest

import pipeline
import scraper
from scrapers import moneydj


ETF_CODE = "00406A"
TARGET_DATE = date(2026, 7, 15)
RUN_AT = datetime(2026, 7, 15, 15, 0, tzinfo=pipeline.TAIPEI_TIMEZONE)


def make_rows(total_weight: float, row_date: str = "2026/07/15") -> list[dict]:
    weight = total_weight / 5
    return [
        {
            "date": row_date,
            "etf_code": ETF_CODE,
            "asset_name": f"測試股票{i}({1000 + i}.TW)",
            "asset_type": "stock",
            "stock_code": str(1000 + i),
            "stock_name": f"測試股票{i}",
            "shares": 1000,
            "weight_pct": weight,
            "source_url": "https://example.test",
            "source_type": "moneydj_primary",
            "extraction_method": "test",
        }
        for i in range(5)
    ]


def make_result(total_weight: float, *, source_type: str = "moneydj_primary") -> dict:
    rows = make_rows(total_weight)
    warning = None
    if total_weight < moneydj.WARNING_MIN_TOTAL_WEIGHT:
        warning = {
            "reason": "total_weight_below_expected_range",
            "source_total_weight_all_rows": round(total_weight, 2),
            "minimum_expected_weight": moneydj.WARNING_MIN_TOTAL_WEIGHT,
            "maximum_expected_weight": moneydj.WARNING_MAX_TOTAL_WEIGHT,
        }
    elif total_weight > moneydj.WARNING_MAX_TOTAL_WEIGHT:
        warning = {
            "reason": "total_weight_above_expected_range",
            "source_total_weight_all_rows": round(total_weight, 2),
            "minimum_expected_weight": moneydj.WARNING_MIN_TOTAL_WEIGHT,
            "maximum_expected_weight": moneydj.WARNING_MAX_TOTAL_WEIGHT,
        }
    result = {
        "ok": True,
        "reason": "ok",
        "all_rows": rows,
        "stock_rows": rows,
        "non_stock_rows": [],
        "source_url": "https://example.test",
        "source_type": source_type,
        "total_weight_all_rows": round(total_weight, 2),
        "total_weight_stock_rows": round(total_weight, 2),
    }
    if warning:
        result["weight_warning"] = warning
    return result


def scrape_moneydj_with_rows(rows: list[dict]) -> dict:
    with patch("scrapers.moneydj.fetch_html", return_value="<html></html>"), patch(
        "scrapers.moneydj.parse_moneydj_rows", return_value=rows
    ):
        return moneydj.scrape_moneydj(ETF_CODE)


@pytest.mark.parametrize(
    ("total_weight", "expected_reason"),
    [
        (69.99, "total_weight_below_expected_range"),
        (0.05, "total_weight_below_expected_range"),
        (140.01, "total_weight_above_expected_range"),
        (500.0, "total_weight_above_expected_range"),
    ],
    ids=["just-below-min", "far-below-min", "just-above-max", "far-above-max"],
)
def test_weight_threshold_breach_is_warning_not_failure(total_weight, expected_reason):
    result = scrape_moneydj_with_rows(make_rows(total_weight))

    assert result["ok"] is True
    assert result["reason"] == "ok"
    assert result["weight_warning"] == {
        "reason": expected_reason,
        "source_total_weight_all_rows": round(total_weight, 2),
        "minimum_expected_weight": 70.0,
        "maximum_expected_weight": 140.0,
    }


def test_weight_within_current_thresholds_has_no_warning():
    result = scrape_moneydj_with_rows(make_rows(100.0))

    assert result["ok"] is True
    assert result["reason"] == "ok"
    assert "weight_warning" not in result


def test_structural_validation_failure_remains_failure():
    rows = make_rows(100.0)
    rows[0]["date"] = None

    result = scrape_moneydj_with_rows(rows)

    assert result["ok"] is False
    assert result["reason"] == "missing_or_unparseable_date"
    assert "weight_warning" not in result


def test_retry_stops_after_first_structurally_valid_warned_result():
    rows = make_rows(61.98)

    with patch("scrapers.moneydj.fetch_html", return_value="<html></html>") as fetch_html, patch(
        "scrapers.moneydj.parse_moneydj_rows", return_value=rows
    ), patch("scraper.time.sleep") as sleep:
        result = scraper._retry_moneydj(ETF_CODE)

    assert result["ok"] is True
    assert result["weight_warning"]["reason"] == "total_weight_below_expected_range"
    fetch_html.assert_called_once()
    sleep.assert_not_called()


def test_sync_scraper_accepts_warned_moneydj_without_weight_fallback():
    warned = make_result(61.98)

    with patch("scraper._retry_moneydj", return_value=warned), patch(
        "scraper._official_fallback_static"
    ) as official_fallback, patch(
        "scraper.get_historical_mean_stock_row_count", return_value=None
    ):
        result = scraper.scrape_holdings(ETF_CODE, target_date=TARGET_DATE)

    official_fallback.assert_not_called()
    assert result["source_type"] == "moneydj_primary"
    assert result["weight_warning"] == warned["weight_warning"]


@pytest.mark.asyncio
async def test_async_scraper_accepts_warned_moneydj_without_browser_or_official_fallback():
    warned = make_result(141.0)
    moneydj_browser = AsyncMock()
    official_fallback = AsyncMock()

    with patch("scraper._retry_moneydj_async", new=AsyncMock(return_value=warned)), patch(
        "scraper.scrape_moneydj_browser", new=moneydj_browser
    ), patch(
        "scraper._official_fallback_with_browser", new=official_fallback
    ), patch(
        "scraper.get_historical_mean_stock_row_count", return_value=None
    ):
        result = await scraper.scrape_holdings_with_browser_async(
            ETF_CODE,
            object(),
            target_date=TARGET_DATE,
        )

    moneydj_browser.assert_not_awaited()
    official_fallback.assert_not_awaited()
    assert result["source_type"] == "moneydj_primary"
    assert result["weight_warning"] == warned["weight_warning"]


def test_pipeline_summary_surfaces_weight_warning_without_diagnostic_rescrape():
    warned = make_result(61.98)
    diagnostic_scrape = Mock()

    with patch("pipeline.replace_daily_snapshot", return_value={"inserted": True}), patch(
        "scrapers.moneydj.scrape_moneydj", diagnostic_scrape
    ):
        summary = pipeline._run_scrape_sync(
            ":memory:",
            [{"code": ETF_CODE}],
            lambda etf_code, target_date: warned,
            use_trading_calendar=False,
            run_at=RUN_AT,
        )

    assert summary["moneydj_success"] == 1
    assert summary["failed"] == 0
    assert summary["weight_warnings"] == [
        {"etf_code": ETF_CODE, **warned["weight_warning"]}
    ]
    diagnostic_scrape.assert_not_called()


def test_row_count_validation_can_still_replace_warned_moneydj_result():
    warned = make_result(61.98)
    official = make_result(100.0, source_type="official_fallback")
    official_rows = []
    for i in range(8):
        row = {**official["all_rows"][i % 5]}
        row["stock_code"] = str(2000 + i)
        row["stock_name"] = f"官方股票{i}"
        row["asset_name"] = f"官方股票{i}({2000 + i}.TW)"
        official_rows.append(row)
    official["all_rows"] = official_rows
    official["stock_rows"] = official_rows

    with patch("scraper._retry_moneydj", return_value=warned), patch(
        "scraper.get_historical_mean_stock_row_count", return_value=10
    ), patch(
        "scraper._official_fallback_static", return_value=official
    ) as official_fallback:
        result = scraper.scrape_holdings(ETF_CODE, target_date=TARGET_DATE)

    official_fallback.assert_called_once_with(ETF_CODE)
    assert result["source_type"] == "official_fallback"
    assert len(result["stock_rows"]) == 8
