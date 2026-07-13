from datetime import date
from unittest.mock import AsyncMock, patch

import pytest


class FixedDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 7, 7)


def make_success(etf_code, row_date="2026/07/07"):
    row = {
        "date": row_date,
        "etf_code": etf_code,
        "asset_name": "台積電(2330.TW)",
        "asset_type": "stock",
        "stock_code": "2330",
        "stock_name": "台積電",
        "shares": 1000,
        "weight_pct": 10.0,
        "source_url": "https://example.test",
        "source_type": "moneydj_browser",
        "extraction_method": "requests_bs4",
    }
    return {
        "ok": True,
        "reason": "ok",
        "all_rows": [row],
        "stock_rows": [row],
        "non_stock_rows": [],
        "source_url": "https://example.test",
        "source_type": "moneydj_browser",
        "total_weight_all_rows": 10.0,
        "total_weight_stock_rows": 10.0,
    }


def _snapshot_write_ok(*args, **kwargs):
    return {"inserted": True, "source_type": "moneydj_browser"}


@pytest.mark.asyncio
async def test_run_selected_scrape_with_browser_async_retries_only_requested_codes():
    from pipeline import run_selected_scrape_with_browser_async

    page = object()
    requested_codes = ["00401A", "00402A"]
    scraper = AsyncMock(side_effect=lambda code, page_arg, target_date=None: make_success(code))

    with patch("pipeline.date", FixedDate), \
        patch("pipeline.scrape_holdings_with_browser_async", scraper), \
        patch("pipeline.init_db") as init_db, \
        patch("pipeline.replace_daily_snapshot", side_effect=_snapshot_write_ok) as replace_daily_snapshot, \
        patch("pipeline.insert_scrape_run") as insert_scrape_run:
        summary = await run_selected_scrape_with_browser_async(":memory:", requested_codes, page=page)

    init_db.assert_called_once_with(":memory:")
    assert [call.args[0] for call in scraper.await_args_list] == requested_codes
    assert {call.args[1] for call in scraper.await_args_list} == {page}
    assert summary["date"] == "2026-07-07"
    assert summary["total_etfs"] == 2
    assert summary["data_freshness"] == {"fresh": 2, "stale": 0, "unknown": 0}
    assert replace_daily_snapshot.call_count == 2
    assert insert_scrape_run.call_count == 2


@pytest.mark.asyncio
async def test_run_selected_scrape_with_browser_async_can_use_explicit_run_date():
    """Regression: retrying a prior report date must not depend on date.today()."""
    from pipeline import run_selected_scrape_with_browser_async

    page = object()
    requested_codes = ["00401A"]
    scraper = AsyncMock(side_effect=lambda code, page_arg, target_date=None: make_success(code, row_date="2026/07/06"))

    with patch("pipeline.date", FixedDate), \
        patch("pipeline.scrape_holdings_with_browser_async", scraper), \
        patch("pipeline.init_db"), \
        patch("pipeline.replace_daily_snapshot", side_effect=_snapshot_write_ok), \
        patch("pipeline.insert_scrape_run") as insert_scrape_run:
        summary = await run_selected_scrape_with_browser_async(
            ":memory:",
            requested_codes,
            page=page,
            run_date=date(2026, 7, 6),
        )

    assert summary["date"] == "2026-07-06"
    assert summary["data_freshness"] == {"fresh": 1, "stale": 0, "unknown": 0}
    assert insert_scrape_run.call_args.args[0].date == date(2026, 7, 6)
    assert insert_scrape_run.call_args.args[0].data_date == date(2026, 7, 6)
