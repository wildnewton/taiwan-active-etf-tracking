from datetime import date, datetime
from unittest.mock import patch

import db
import pipeline
from models import HoldingRow, NonStockAssetRow, ScrapeRun


RUN_DATE = date(2026, 7, 14)
PREVIOUS_DATE = date(2026, 7, 13)
BEFORE_CUTOFF = datetime(
    2026,
    7,
    14,
    14,
    0,
    tzinfo=pipeline.TAIPEI_TIMEZONE,
)


def _run(
    etf_code: str,
    *,
    status: str = "success",
    scrape_date: date = PREVIOUS_DATE,
    data_date: date = PREVIOUS_DATE,
) -> ScrapeRun:
    return ScrapeRun(
        date=scrape_date,
        data_date=data_date,
        etf_code=etf_code,
        status=status,
        primary_source="moneydj_primary",
        primary_success=status == "success",
        moneydj_browser_used=False,
        official_fallback_used=False,
        official_success=False,
        rows_extracted=1,
        stock_rows_extracted=1,
        non_stock_rows_extracted=0,
        total_weight_all_rows=10.0,
        total_weight_stock_rows=10.0,
        source_url="https://example.test",
        error=None if status == "success" else "failed",
        started_at=datetime(2026, 7, 13, 15, 0),
        finished_at=datetime(2026, 7, 13, 15, 1),
    )


def _holding(etf_code: str) -> HoldingRow:
    return HoldingRow(
        date=PREVIOUS_DATE,
        etf_code=etf_code,
        asset_name="台積電(2330.TW)",
        asset_type="stock",
        stock_code="2330",
        stock_name="台積電",
        shares=1000,
        weight_pct=10.0,
        source_url="https://example.test",
        source_type="moneydj_primary",
        extraction_method="test",
        scraped_at=datetime(2026, 7, 13, 15, 0),
    )


def test_before_cutoff_reuses_previous_trading_day_validated_snapshot(tmp_path):
    db_path = tmp_path / "cutoff.sqlite"
    db.init_db(str(db_path))
    db.insert_holdings([_holding("00980A")])
    db.insert_scrape_run(_run("00980A"))

    def must_not_scrape(etf_code, target_date):
        raise AssertionError(f"unexpected scrape for {etf_code} at {target_date}")

    with patch(
        "pipeline.latest_tw_trading_day_on_or_before",
        return_value=PREVIOUS_DATE,
    ), patch("pipeline.is_tw_trading_day", return_value=True):
        summary = pipeline._run_scrape_sync(
            str(db_path),
            [{"code": "00980A"}],
            must_not_scrape,
            already_initialized=True,
            run_at=BEFORE_CUTOFF,
        )

    assert summary["expected_data_date"] == PREVIOUS_DATE.isoformat()
    assert summary["preexisting_success"] == 1
    assert summary["data_freshness"] == {"fresh": 1, "stale": 0, "unknown": 0}


def test_failed_run_does_not_validate_existing_snapshot(tmp_path):
    db_path = tmp_path / "failed.sqlite"
    db.init_db(str(db_path))
    db.insert_holdings([_holding("00980A")])
    db.insert_scrape_run(_run("00980A", status="failed"))

    assert db.successful_snapshot_exists(PREVIOUS_DATE, "00980A") is False


def test_successful_non_stock_only_snapshot_is_eligible(tmp_path):
    db_path = tmp_path / "non-stock.sqlite"
    db.init_db(str(db_path))
    db.insert_non_stock_assets([
        NonStockAssetRow(
            date=PREVIOUS_DATE,
            etf_code="00980A",
            asset_name="現金",
            asset_type="cash",
            weight_pct=100.0,
            source_url="https://example.test",
            source_type="moneydj_primary",
            extraction_method="test",
            scraped_at=datetime(2026, 7, 13, 15, 0),
        )
    ])
    db.insert_scrape_run(_run("00980A"))

    assert db.successful_snapshot_exists(PREVIOUS_DATE, "00980A") is True
