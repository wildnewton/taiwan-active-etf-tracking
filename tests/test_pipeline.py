from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

import db
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


def _active_codes():
    return TEST_ETF_CODES


def _active_count():
    return len(_active_codes())


def _patch_active_etfs():
    return patch("pipeline._active_etfs_for_run", return_value=TEST_ETFS)


def _patch_run_date(date_cls=FixedDate):
    return patch("pipeline.date", date_cls)


def make_row(etf_code, asset_type="stock", stock_code="2330", asset_name=None, row_date="2026/06/22"):
    return {
        "date": row_date,
        "etf_code": etf_code,
        "asset_name": asset_name or f"台積電({stock_code}.TW)",
        "asset_type": asset_type,
        "stock_code": stock_code if asset_type == "stock" else None,
        "stock_name": "台積電" if asset_type == "stock" else None,
        "shares": 1000,
        "weight_pct": 10.0,
        "source_url": "https://example.test",
        "source_type": "moneydj_primary",
        "extraction_method": "requests_bs4",
    }


def make_non_stock_row(etf_code, asset_type="cash", asset_name="現金", row_date="2026/06/22"):
    return {
        "date": row_date,
        "etf_code": etf_code,
        "asset_name": asset_name,
        "asset_type": asset_type,
        "weight_pct": 10.0,
        "source_url": "https://example.test",
        "source_type": "moneydj_primary",
        "extraction_method": "requests_bs4",
    }


def make_success(etf_code, source_type="moneydj_primary", row_date="2026/06/22"):
    stock_row = make_row(etf_code, row_date=row_date)
    non_stock_row = make_non_stock_row(etf_code, row_date=row_date)
    non_stock_row["source_type"] = source_type
    stock_row["source_type"] = source_type
    return {
        "ok": True,
        "reason": "ok",
        "all_rows": [stock_row, non_stock_row],
        "stock_rows": [stock_row],
        "non_stock_rows": [non_stock_row],
        "source_url": "https://example.test",
        "source_type": source_type,
        "total_weight_all_rows": 20.0,
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
    expected_count = _active_count()
    with _patch_run_date(), \
        _patch_active_etfs(), \
        patch("pipeline.scrape_holdings", side_effect=lambda code: make_success(code)) as scrape, \
        patch("pipeline.init_db") as init_db, \
        patch("pipeline.insert_holdings") as insert_holdings, \
        patch("pipeline.insert_non_stock_assets") as insert_non_stock_assets, \
        patch("pipeline.insert_scrape_run") as insert_scrape_run:
        summary = run_daily_scrape(":memory:")

    assert scrape.call_count == expected_count
    assert summary["date"] == "2026-06-22"
    assert "data_date" not in summary
    assert summary["data_freshness"] == {"fresh": expected_count, "stale": 0, "unknown": 0}
    assert summary["stale_etfs"] == []
    assert summary["unknown_date_etfs"] == []
    assert summary["data_date_min"] == "2026-06-22"
    assert summary["data_date_max"] == "2026-06-22"
    assert summary["total_etfs"] == expected_count
    assert summary["moneydj_success"] == expected_count
    assert summary["official_success"] == 0
    assert summary["failed"] == 0
    assert summary["total_stock_rows"] == expected_count
    assert summary["total_non_stock_rows"] == expected_count
    assert summary["failures"] == []
    init_db.assert_called_once_with(":memory:")
    assert insert_holdings.call_count == expected_count
    assert insert_non_stock_assets.call_count == expected_count
    assert insert_scrape_run.call_count == expected_count


@pytest.mark.asyncio
async def test_run_daily_scrape_with_browser_async_uses_browser_decision_tree():
    expected_count = _active_count()
    page = object()
    scraper = AsyncMock(side_effect=lambda code, page_arg: make_success(code, source_type="moneydj_browser"))

    with _patch_run_date(), \
        _patch_active_etfs(), \
        patch("pipeline.scrape_holdings_with_browser_async", scraper), \
        patch("pipeline.init_db") as init_db, \
        patch("pipeline.insert_holdings") as insert_holdings, \
        patch("pipeline.insert_non_stock_assets") as insert_non_stock_assets, \
        patch("pipeline.insert_scrape_run") as insert_scrape_run:
        summary = await run_daily_scrape_with_browser_async(":memory:", page=page)

    assert scraper.await_count == expected_count
    assert [call.args[0] for call in scraper.await_args_list] == _active_codes()
    assert {call.args[1] for call in scraper.await_args_list} == {page}
    assert summary["date"] == "2026-06-22"
    assert "data_date" not in summary
    assert summary["data_freshness"] == {"fresh": expected_count, "stale": 0, "unknown": 0}
    assert summary["total_etfs"] == expected_count
    assert summary["moneydj_success"] == expected_count
    assert summary["official_success"] == 0
    assert summary["failed"] == 0
    assert summary["total_stock_rows"] == expected_count
    assert summary["total_non_stock_rows"] == expected_count
    init_db.assert_called_once_with(":memory:")
    assert insert_holdings.call_count == expected_count
    assert insert_non_stock_assets.call_count == expected_count
    assert insert_scrape_run.call_count == expected_count


def test_run_daily_scrape_some_fail():
    failed_codes = set(_active_codes()[:2])
    expected_count = _active_count()

    def fake_scrape(code):
        if code in failed_codes:
            return make_failure("blocked")
        return make_success(code, source_type="official_fallback")

    with _patch_run_date(), \
        _patch_active_etfs(), \
        patch("pipeline.scrape_holdings", side_effect=fake_scrape), \
        patch("pipeline.init_db"), \
        patch("pipeline.insert_holdings"), \
        patch("pipeline.insert_non_stock_assets"), \
        patch("pipeline.insert_scrape_run"):
        summary = run_daily_scrape(":memory:")

    assert summary["total_etfs"] == expected_count
    assert summary["moneydj_success"] == 0
    assert summary["official_success"] == expected_count - len(failed_codes)
    assert summary["failed"] == len(failed_codes)
    assert summary["data_freshness"] == {"fresh": expected_count - len(failed_codes), "stale": 0, "unknown": 0}
    assert len(summary["failures"]) == len(failed_codes)
    assert {failure["etf_code"] for failure in summary["failures"]} == failed_codes
    assert all(failure["reason"] == "blocked" for failure in summary["failures"])


def test_run_daily_scrape_saves_to_db():
    expected_count = _active_count()
    with _patch_run_date(), \
        _patch_active_etfs(), \
        patch("pipeline.scrape_holdings", side_effect=lambda code: make_success(code)):
        summary = run_daily_scrape(":memory:")

    with db._connect() as conn:
        holding_count = conn.execute("SELECT COUNT(*) FROM etf_daily_holdings").fetchone()[0]
        non_stock_count = conn.execute(
            "SELECT COUNT(*) FROM etf_daily_non_stock_assets"
        ).fetchone()[0]

    assert summary["total_stock_rows"] == expected_count
    assert summary["total_non_stock_rows"] == expected_count
    assert holding_count == expected_count
    assert non_stock_count == expected_count


def test_run_daily_scrape_logs_scrape_runs():
    expected_count = _active_count()
    with _patch_run_date(), \
        _patch_active_etfs(), \
        patch("pipeline.scrape_holdings", side_effect=lambda code: make_success(code)):
        run_daily_scrape(":memory:")

    with db._connect() as conn:
        rows = conn.execute(
            """
            SELECT etf_code, status, primary_source, primary_success, date, data_date
            FROM etf_scrape_runs
            ORDER BY etf_code
            """
        ).fetchall()

    assert len(rows) == expected_count
    assert {row[1] for row in rows} == {"success"}
    assert {row[2] for row in rows} == {"moneydj_primary"}
    assert {row[3] for row in rows} == {1}
    assert {row[4] for row in rows} == {"2026-06-22"}
    assert {row[5] for row in rows} == {"2026-06-22"}


def test_scrape_run_no_run_id():
    """ScrapeRun dataclass should no longer have a run_id field."""
    from dataclasses import fields
    from models import ScrapeRun

    field_names = [f.name for f in fields(ScrapeRun)]
    assert "run_id" not in field_names
    assert field_names[0] == "date"
    assert field_names[1] == "data_date"
    assert field_names[2] == "etf_code"


def test_insert_scrape_run_no_duplicates():
    """Inserting the same (date, etf_code) twice should not create duplicates."""
    from datetime import date, datetime
    from models import ScrapeRun

    db.init_db(":memory:")
    run = ScrapeRun(
        date=date(2026, 6, 22),
        data_date=date(2026, 6, 22),
        etf_code="00980A",
        status="success",
        primary_source="moneydj_primary",
        primary_success=True,
        moneydj_browser_used=False,
        official_fallback_used=False,
        official_success=False,
        rows_extracted=10,
        stock_rows_extracted=8,
        non_stock_rows_extracted=2,
        total_weight_all_rows=100.0,
        total_weight_stock_rows=95.0,
        source_url="https://example.test",
        error=None,
        started_at=datetime(2026, 6, 22, 9, 0),
        finished_at=datetime(2026, 6, 22, 9, 1),
    )

    db.insert_scrape_run(run)
    db.insert_scrape_run(run)  # second insert should be ignored

    with db._connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM etf_scrape_runs WHERE etf_code = '00980A'"
        ).fetchone()[0]

    assert count == 1


def test_insert_scrape_run_replaces_failure_with_success():
    """A later success should overwrite an earlier failure for the same (date, etf_code).

    This is the real-world scenario: run A fails 00400A (writes failure record),
    run B succeeds 00400A (should REPLACE the failure with success).
    """
    from datetime import date, datetime
    from models import ScrapeRun

    db.init_db(":memory:")

    failure = ScrapeRun(
        date=date(2026, 6, 24),
        data_date=None,
        etf_code="00400A",
        status="failed",
        primary_source="moneydj_primary",
        primary_success=False,
        moneydj_browser_used=False,
        official_fallback_used=False,
        official_success=False,
        rows_extracted=0,
        stock_rows_extracted=0,
        non_stock_rows_extracted=0,
        total_weight_all_rows=0.0,
        total_weight_stock_rows=0.0,
        source_url="",
        error="all sources failed",
        started_at=datetime(2026, 6, 24, 20, 5, 10),
        finished_at=datetime(2026, 6, 24, 20, 5, 15),
    )

    success = ScrapeRun(
        date=date(2026, 6, 24),
        data_date=date(2026, 6, 24),
        etf_code="00400A",
        status="success",
        primary_source="moneydj_primary",
        primary_success=True,
        moneydj_browser_used=False,
        official_fallback_used=False,
        official_success=False,
        rows_extracted=54,
        stock_rows_extracted=54,
        non_stock_rows_extracted=0,
        total_weight_all_rows=93.6,
        total_weight_stock_rows=93.6,
        source_url="https://www.moneydj.com/ETF/X/Basic/Basic0007B.xdjhtm?etfid=00400A.TW",
        error=None,
        started_at=datetime(2026, 6, 24, 23, 31, 26),
        finished_at=datetime(2026, 6, 24, 23, 31, 28),
    )

    db.insert_scrape_run(failure)
    db.insert_scrape_run(success)  # should REPLACE the failure

    with db._connect() as conn:
        row = conn.execute(
            "SELECT status, rows_extracted, error, data_date FROM etf_scrape_runs WHERE etf_code = '00400A'"
        ).fetchone()

    assert row[0] == "success", f"Expected 'success' but got '{row[0]}'"
    assert row[1] == 54, f"Expected 54 rows but got {row[1]}"
    assert row[2] is None, f"Expected no error but got '{row[2]}'"
    assert row[3] == "2026-06-24"


def test_run_daily_scrape_uses_run_date_not_first_source_data_date():
    with _patch_run_date(NextRunDate), \
        _patch_active_etfs(), \
        patch("pipeline.scrape_holdings", side_effect=lambda code: make_success(code, row_date="2026/06/22")):
        summary = run_daily_scrape(":memory:")

    assert summary["date"] == "2026-06-23"
    assert "data_date" not in summary
    assert summary["data_freshness"] == {"fresh": 0, "stale": _active_count(), "unknown": 0}


def test_scrape_run_records_per_etf_data_date_not_first_success_date():
    captured_runs = []

    def fake_scrape(code):
        if code == "00980A":
            return make_success(code, row_date="2026/06/22")
        return make_success(code, row_date="2026/06/23")

    def capture_run(run):
        captured_runs.append(run)

    with _patch_run_date(NextRunDate), \
        _patch_active_etfs(), \
        patch("pipeline.scrape_holdings", side_effect=fake_scrape), \
        patch("pipeline.init_db"), \
        patch("pipeline.insert_holdings"), \
        patch("pipeline.insert_non_stock_assets"), \
        patch("pipeline.insert_scrape_run", side_effect=capture_run):
        summary = run_daily_scrape(":memory:")

    assert len(captured_runs) == _active_count()
    by_code = {run.etf_code: run for run in captured_runs}
    assert by_code["00980A"].date == NEXT_RUN_DATE
    assert by_code["00980A"].data_date == date(2026, 6, 22)
    assert by_code["00981A"].date == NEXT_RUN_DATE
    assert by_code["00981A"].data_date == date(2026, 6, 23)
    assert summary["data_freshness"] == {"fresh": 2, "stale": 1, "unknown": 0}
    assert summary["stale_etfs"] == [
        {
            "etf_code": "00980A",
            "data_date": "2026-06-22",
            "source_type": "moneydj_primary",
            "reason": "source_date_before_run_date",
        }
    ]
    assert summary["data_date_min"] == "2026-06-22"
    assert summary["data_date_max"] == "2026-06-23"


def test_run_daily_scrape_reports_unknown_data_date_without_top_level_data_date():
    unknown = make_success("00980A")
    for row in unknown["all_rows"]:
        row["date"] = ""

    def fake_scrape(code):
        if code == "00980A":
            return unknown
        return make_success(code)

    with _patch_run_date(), \
        _patch_active_etfs(), \
        patch("pipeline.scrape_holdings", side_effect=fake_scrape), \
        patch("pipeline.init_db"), \
        patch("pipeline.insert_holdings"), \
        patch("pipeline.insert_non_stock_assets"), \
        patch("pipeline.insert_scrape_run"):
        summary = run_daily_scrape(":memory:")

    assert "data_date" not in summary
    assert summary["data_freshness"] == {"fresh": 2, "stale": 0, "unknown": 1}
    assert summary["unknown_date_etfs"] == [
        {
            "etf_code": "00980A",
            "source_type": "moneydj_primary",
            "reason": "missing_or_unparseable_source_date",
        }
    ]
