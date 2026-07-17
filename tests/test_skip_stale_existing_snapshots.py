from datetime import date, datetime
from unittest.mock import patch

import db
import pipeline
from models import HoldingRow
from pipeline import run_daily_scrape


RUN_DATE = date(2026, 6, 23)
STALE_DATA_DATE = date(2026, 6, 22)
RUN_AT = datetime.combine(
    RUN_DATE,
    pipeline.DATA_AVAILABILITY_CUTOFF,
    tzinfo=pipeline.TAIPEI_TIMEZONE,
)
ETFS = [{"code": "00980A"}]


def make_success(row_date="2026/06/22"):
    row = {
        "date": row_date,
        "etf_code": "00980A",
        "asset_name": "台積電(2330.TW)",
        "asset_type": "stock",
        "stock_code": "2330",
        "stock_name": "台積電",
        "shares": 1000,
        "weight_pct": 10.0,
        "source_url": "https://example.test",
        "source_type": "moneydj_primary",
        "extraction_method": "requests_bs4",
    }
    return {
        "ok": True,
        "reason": "ok",
        "all_rows": [row],
        "stock_rows": [row],
        "non_stock_rows": [],
        "source_url": "https://example.test",
        "source_type": "moneydj_primary",
        "total_weight_all_rows": 10.0,
        "total_weight_stock_rows": 10.0,
    }


def _base_patches(result):
    return (
        patch("pipeline._current_run_at", return_value=RUN_AT),
        patch("pipeline.latest_tw_trading_day_on_or_before", return_value=RUN_DATE),
        patch("pipeline.is_tw_trading_day", return_value=True),
        patch("pipeline._active_etfs_for_run", return_value=ETFS),
        patch("pipeline.scrape_holdings", return_value=result),
        patch("pipeline.init_db"),
    )


def test_stale_result_with_existing_snapshot_skips_holding_replacement():
    patches = _base_patches(make_success())
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patch(
        "pipeline.snapshot_exists",
        side_effect=lambda data_date, _: data_date == STALE_DATA_DATE,
    ) as snapshot_exists, patch(
        "pipeline.replace_daily_snapshot"
    ) as replace_snapshot:
        summary = run_daily_scrape(":memory:")

    assert snapshot_exists.call_args_list[-1].args == (STALE_DATA_DATE, "00980A")
    replace_snapshot.assert_not_called()
    assert summary["skipped_stale_existing"] == 1
    assert summary["stale_existing_etfs"][0]["data_date"] == "2026-06-22"
    assert summary["data_freshness"] == {"fresh": 0, "stale": 1, "unknown": 0}


def test_stale_result_without_existing_snapshot_writes_once():
    patches = _base_patches(make_success())
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patch(
        "pipeline.snapshot_exists", return_value=False
    ), patch(
        "pipeline.replace_daily_snapshot", return_value={"inserted": True}
    ) as replace_snapshot:
        summary = run_daily_scrape(":memory:")

    replace_snapshot.assert_called_once()
    assert summary["skipped_stale_existing"] == 0
    assert summary["data_freshness"] == {"fresh": 0, "stale": 1, "unknown": 0}


def test_fresh_result_writes_target_snapshot():
    patches = _base_patches(make_success("2026/06/23"))
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patch(
        "pipeline.snapshot_exists", return_value=False
    ) as snapshot_exists, patch(
        "pipeline.replace_daily_snapshot", return_value={"inserted": True}
    ) as replace_snapshot:
        summary = run_daily_scrape(":memory:")

    assert snapshot_exists.call_count == 1
    replace_snapshot.assert_called_once()
    assert summary["data_freshness"] == {"fresh": 1, "stale": 0, "unknown": 0}


def test_snapshot_exists_detects_existing_stock_snapshot():
    db.init_db(":memory:")
    assert db.snapshot_exists(STALE_DATA_DATE, "00980A") is False
    db.insert_holdings([
        HoldingRow(
            date=STALE_DATA_DATE,
            etf_code="00980A",
            asset_name="台積電(2330.TW)",
            asset_type="stock",
            stock_code="2330",
            stock_name="台積電",
            shares=1000,
            weight_pct=100.0,
            source_url="https://example.test",
            source_type="moneydj_primary",
            extraction_method="requests_bs4",
            scraped_at=datetime(2026, 6, 23, 19, 30),
        )
    ])
    assert db.snapshot_exists(STALE_DATA_DATE, "00980A") is True
