from contextlib import contextmanager
from datetime import date, datetime
from unittest.mock import AsyncMock, patch

import pytest

import db
import pipeline
from pipeline import run_daily_scrape, run_daily_scrape_with_browser_async


RUN_DATE = date(2026, 6, 22)
NEXT_RUN_DATE = date(2026, 6, 23)
TEST_ETF_CODES = ["00980A", "00981A", "00982A"]
TEST_ETFS = [{"code": code} for code in TEST_ETF_CODES]


class FixedDate(date):
    @classmethod
    def today(cls):
        return cls(RUN_DATE.year, RUN_DATE.month, RUN_DATE.day)


class NextRunDate(date):
    @classmethod
    def today(cls):
        return cls(NEXT_RUN_DATE.year, NEXT_RUN_DATE.month, NEXT_RUN_DATE.day)


@contextmanager
def _patch_run_date(date_cls=FixedDate):
    run_at = datetime.combine(
        date_cls.today(),
        pipeline.DATA_AVAILABILITY_CUTOFF,
        tzinfo=pipeline.TAIPEI_TIMEZONE,
    )
    with patch("pipeline.date", date_cls), patch(
        "pipeline._current_run_at", return_value=run_at
    ):
        yield


def _patch_active_etfs():
    return patch("pipeline._active_etfs_for_run", return_value=TEST_ETFS)


def make_row(etf_code, row_date="2026/06/22", source_type="moneydj_primary"):
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


def make_success(etf_code, source_type="moneydj_primary", row_date="2026/06/22"):
    row = make_row(etf_code, row_date=row_date, source_type=source_type)
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


def make_failure(reason="all sources failed"):
    return {
        "ok": False,
        "reason": reason,
        "all_rows": [],
        "stock_rows": [],
        "non_stock_rows": [],
        "source_url": "",
        "source_type": "",
        "total_weight_all_rows": 0.0,
        "total_weight_stock_rows": 0.0,
    }


def test_run_daily_scrape_all_success():
    with _patch_run_date(), _patch_active_etfs(), patch(
        "pipeline.scrape_holdings",
        side_effect=lambda code, target_date=None: make_success(code),
    ) as scrape, patch("pipeline.init_db") as init_db, patch(
        "pipeline.replace_daily_snapshot", return_value={"inserted": True}
    ) as replace_snapshot:
        summary = run_daily_scrape(":memory:")

    assert scrape.call_count == 3
    assert summary["date"] == "2026-06-22"
    assert summary["data_freshness"] == {"fresh": 3, "stale": 0, "unknown": 0}
    assert summary["moneydj_success"] == 3
    assert summary["failed"] == 0
    assert summary["data_date_min"] == "2026-06-22"
    assert summary["data_date_max"] == "2026-06-22"
    init_db.assert_called_once_with(":memory:")
    assert replace_snapshot.call_count == 3


@pytest.mark.asyncio
async def test_run_daily_scrape_with_browser_async_uses_browser_decision_tree():
    page = object()
    scraper = AsyncMock(
        side_effect=lambda code, page_arg, target_date=None: make_success(
            code, source_type="moneydj_browser"
        )
    )
    with _patch_run_date(), _patch_active_etfs(), patch(
        "pipeline.scrape_holdings_with_browser_async", scraper
    ), patch("pipeline.init_db"), patch(
        "pipeline.replace_daily_snapshot", return_value={"inserted": True}
    ) as replace_snapshot:
        summary = await run_daily_scrape_with_browser_async(":memory:", page=page)

    assert [call.args[0] for call in scraper.await_args_list] == TEST_ETF_CODES
    assert {call.args[1] for call in scraper.await_args_list} == {page}
    assert summary["data_freshness"] == {"fresh": 3, "stale": 0, "unknown": 0}
    assert replace_snapshot.call_count == 3


def test_run_daily_scrape_some_fail():
    failed_codes = {"00980A", "00981A"}

    def fake_scrape(code, target_date=None):
        return make_failure("blocked") if code in failed_codes else make_success(code)

    with _patch_run_date(), _patch_active_etfs(), patch(
        "pipeline.scrape_holdings", side_effect=fake_scrape
    ), patch("pipeline.init_db"), patch(
        "pipeline.replace_daily_snapshot", return_value={"inserted": True}
    ) as replace_snapshot:
        summary = run_daily_scrape(":memory:")

    assert summary["failed"] == 2
    assert summary["moneydj_success"] == 1
    assert {row["etf_code"] for row in summary["failures"]} == failed_codes
    assert replace_snapshot.call_count == 1


def test_run_daily_scrape_saves_only_canonical_holdings():
    with _patch_run_date(), _patch_active_etfs(), patch(
        "pipeline.scrape_holdings",
        side_effect=lambda code, target_date=None: make_success(code),
    ):
        run_daily_scrape(":memory:")

    with db._connect() as conn:
        holdings = conn.execute("SELECT COUNT(*) FROM etf_daily_holdings").fetchone()[0]
        scrape_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='etf_scrape_runs'"
        ).fetchone()
    assert holdings == 3
    assert scrape_table is None


def test_run_daily_scrape_uses_run_date_not_source_data_date():
    with _patch_run_date(NextRunDate), _patch_active_etfs(), patch(
        "pipeline.scrape_holdings",
        side_effect=lambda code, target_date=None: make_success(
            code, row_date="2026/06/22"
        ),
    ):
        summary = run_daily_scrape(":memory:")

    assert summary["date"] == "2026-06-23"
    assert summary["data_freshness"] == {"fresh": 0, "stale": 3, "unknown": 0}
    assert summary["data_date_min"] == "2026-06-22"
    assert summary["data_date_max"] == "2026-06-22"


def test_mixed_source_dates_are_preserved_in_summary_and_holdings():
    def fake_scrape(code, target_date=None):
        row_date = "2026/06/22" if code == "00980A" else "2026/06/23"
        return make_success(code, row_date=row_date)

    with _patch_run_date(NextRunDate), _patch_active_etfs(), patch(
        "pipeline.scrape_holdings", side_effect=fake_scrape
    ):
        summary = run_daily_scrape(":memory:")

    assert summary["data_freshness"] == {"fresh": 2, "stale": 1, "unknown": 0}
    assert summary["stale_etfs"][0]["etf_code"] == "00980A"
    assert summary["data_date_min"] == "2026-06-22"
    assert summary["data_date_max"] == "2026-06-23"
    with db._connect() as conn:
        dates = conn.execute(
            "SELECT DISTINCT date FROM etf_daily_holdings ORDER BY date"
        ).fetchall()
    assert dates == [("2026-06-22",), ("2026-06-23",)]


def test_unknown_source_date_is_rejected_without_stopping_later_etfs():
    unknown = make_success("00980A")
    unknown["stock_rows"][0]["date"] = ""

    def fake_scrape(code, target_date=None):
        return unknown if code == "00980A" else make_success(code)

    with _patch_run_date(), _patch_active_etfs(), patch(
        "pipeline.scrape_holdings", side_effect=fake_scrape
    ), patch("pipeline._check_moneydj_warning"):
        summary = run_daily_scrape(":memory:")

    assert summary["data_freshness"] == {"fresh": 2, "stale": 0, "unknown": 1}
    assert summary["unknown_date_etfs"][0]["etf_code"] == "00980A"
    with db._connect() as conn:
        codes = conn.execute(
            "SELECT DISTINCT etf_code FROM etf_daily_holdings ORDER BY etf_code"
        ).fetchall()
    assert codes == [("00981A",), ("00982A",)]
