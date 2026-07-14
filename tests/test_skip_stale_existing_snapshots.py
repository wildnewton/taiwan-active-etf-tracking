from datetime import date, datetime
from unittest.mock import call, patch

import db
import pipeline
from models import HoldingRow
from pipeline import run_daily_scrape


RUN_DATE = date(2026, 6, 23)
STALE_DATA_DATE = date(2026, 6, 22)


class RunDate(date):
    @classmethod
    def today(cls):
        return cls(RUN_DATE.year, RUN_DATE.month, RUN_DATE.day)


ETFS = [{"code": "00980A"}]


def make_row(etf_code="00980A", row_date="2026/06/22", source_type="moneydj_primary"):
    return {
        "date": row_date,
        "etf_code": etf_code,
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


def make_success(row_date="2026/06/22", source_type="moneydj_primary"):
    row = make_row(row_date=row_date, source_type=source_type)
    return {
        "ok": True,
        "reason": "ok",
        "all_rows": [row],
        "stock_rows": [row],
        "non_stock_rows": [],
        "source_url": "https://example.test",
        "source_type": source_type,
        "total_weight_all_rows": 10.0,
        "total_weight_stock_rows": 10.0,
    }


def test_stale_result_with_existing_snapshot_skips_holding_replacement():
    captured_runs = []

    with patch("pipeline.date", RunDate), \
        patch("pipeline._current_run_at", return_value=datetime.combine(
            RunDate.today(),
            pipeline.DATA_AVAILABILITY_CUTOFF,
            tzinfo=pipeline.TAIPEI_TIMEZONE,
        )), \
        patch("pipeline.latest_tw_trading_day_on_or_before", return_value=RUN_DATE), \
        patch("pipeline.is_tw_trading_day", return_value=True), \
        patch("pipeline._active_etfs_for_run", return_value=ETFS), \
        patch("pipeline.scrape_holdings", return_value=make_success(row_date="2026/06/22")), \
        patch("pipeline.successful_snapshot_exists", return_value=False) as successful_snapshot_exists, \
        patch(
            "pipeline.snapshot_exists",
            side_effect=lambda data_date, _: data_date == STALE_DATA_DATE,
        ) as snapshot_exists, \
        patch("pipeline.init_db"), \
        patch("pipeline.replace_daily_snapshot") as replace_daily_snapshot, \
        patch("pipeline.insert_scrape_run", side_effect=captured_runs.append):
        summary = run_daily_scrape(":memory:")

    successful_snapshot_exists.assert_called_once_with(RUN_DATE, "00980A")
    snapshot_exists.assert_called_once_with(STALE_DATA_DATE, "00980A")
    replace_daily_snapshot.assert_not_called()
    assert summary["skipped_stale_existing"] == 1
    assert summary["stale_existing_etfs"] == [
        {
            "etf_code": "00980A",
            "data_date": "2026-06-22",
            "source_type": "moneydj_primary",
            "reason": "stale_snapshot_already_exists",
        }
    ]
    assert summary["data_freshness"] == {"fresh": 0, "stale": 1, "unknown": 0}
    assert len(captured_runs) == 1
    assert captured_runs[0].status == "skipped_stale_existing"
    assert captured_runs[0].data_date == STALE_DATA_DATE
    assert captured_runs[0].error == "stale_snapshot_already_exists"


def test_stale_result_without_existing_snapshot_writes_once():
    with patch("pipeline.date", RunDate), \
        patch("pipeline._current_run_at", return_value=datetime.combine(
            RunDate.today(),
            pipeline.DATA_AVAILABILITY_CUTOFF,
            tzinfo=pipeline.TAIPEI_TIMEZONE,
        )), \
        patch("pipeline.latest_tw_trading_day_on_or_before", return_value=RUN_DATE), \
        patch("pipeline.is_tw_trading_day", return_value=True), \
        patch("pipeline._active_etfs_for_run", return_value=ETFS), \
        patch("pipeline.scrape_holdings", return_value=make_success(row_date="2026/06/22")), \
        patch("pipeline.successful_snapshot_exists", return_value=False) as successful_snapshot_exists, \
        patch("pipeline.snapshot_exists", return_value=False) as snapshot_exists, \
        patch("pipeline.init_db"), \
        patch("pipeline.replace_daily_snapshot", return_value={"inserted": True}) as replace_daily_snapshot, \
        patch("pipeline.insert_scrape_run"):
        summary = run_daily_scrape(":memory:")

    successful_snapshot_exists.assert_called_once_with(RUN_DATE, "00980A")
    snapshot_exists.assert_called_once_with(STALE_DATA_DATE, "00980A")
    replace_daily_snapshot.assert_called_once()
    assert summary["skipped_stale_existing"] == 0
    assert summary["stale_existing_etfs"] == []
    assert summary["data_freshness"] == {"fresh": 0, "stale": 1, "unknown": 0}


def test_fresh_result_does_not_check_for_existing_stale_snapshot():
    with patch("pipeline.date", RunDate), \
        patch("pipeline._current_run_at", return_value=datetime.combine(
            RunDate.today(),
            pipeline.DATA_AVAILABILITY_CUTOFF,
            tzinfo=pipeline.TAIPEI_TIMEZONE,
        )), \
        patch("pipeline.latest_tw_trading_day_on_or_before", return_value=RUN_DATE), \
        patch("pipeline.is_tw_trading_day", return_value=True), \
        patch("pipeline._active_etfs_for_run", return_value=ETFS), \
        patch("pipeline.scrape_holdings", return_value=make_success(row_date="2026/06/23")), \
        patch("pipeline.successful_snapshot_exists", return_value=False) as successful_snapshot_exists, \
        patch("pipeline.snapshot_exists", return_value=False) as snapshot_exists, \
        patch("pipeline.init_db"), \
        patch("pipeline.replace_daily_snapshot", return_value={"inserted": True}) as replace_daily_snapshot, \
        patch("pipeline.insert_scrape_run"):
        summary = run_daily_scrape(":memory:")

    successful_snapshot_exists.assert_called_once_with(RUN_DATE, "00980A")
    snapshot_exists.assert_not_called()
    replace_daily_snapshot.assert_called_once()
    assert summary["skipped_stale_existing"] == 0
    assert summary["stale_existing_etfs"] == []
    assert summary["data_freshness"] == {"fresh": 1, "stale": 0, "unknown": 0}


def test_snapshot_exists_detects_existing_stock_snapshot():
    db.init_db(":memory:")
    assert db.snapshot_exists(date(2026, 6, 22), "00980A") is False

    db.insert_holdings([
        HoldingRow(
            date=date(2026, 6, 22),
            etf_code="00980A",
            asset_name="台積電(2330.TW)",
            asset_type="stock",
            stock_code="2330",
            stock_name="台積電",
            shares=1000,
            weight_pct=10.0,
            source_url="https://example.test",
            source_type="moneydj_primary",
            extraction_method="requests_bs4",
            scraped_at=datetime(2026, 6, 23, 19, 30),
        )
    ])

    assert db.snapshot_exists(date(2026, 6, 22), "00980A") is True
    assert db.snapshot_exists("2026-06-22", "00980A") is True
    assert db.snapshot_exists(date(2026, 6, 23), "00980A") is False
