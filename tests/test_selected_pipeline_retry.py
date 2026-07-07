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


@pytest.mark.asyncio
async def test_run_selected_scrape_with_browser_async_retries_only_requested_codes():
    from pipeline import run_selected_scrape_with_browser_async

    page = object()
    requested_codes = ["00401A", "00402A"]
    scraper = AsyncMock(side_effect=lambda code, page_arg: make_success(code))

    with patch("pipeline.date", FixedDate), \
        patch("pipeline.scrape_holdings_with_browser_async", scraper), \
        patch("pipeline.init_db") as init_db, \
        patch("pipeline.insert_holdings") as insert_holdings, \
        patch("pipeline.insert_non_stock_assets") as insert_non_stock_assets, \
        patch("pipeline.insert_scrape_run") as insert_scrape_run:
        summary = await run_selected_scrape_with_browser_async(":memory:", requested_codes, page=page)

    init_db.assert_called_once_with(":memory:")
    assert [call.args[0] for call in scraper.await_args_list] == requested_codes
    assert {call.args[1] for call in scraper.await_args_list} == {page}
    assert summary["date"] == "2026-07-07"
    assert summary["total_etfs"] == 2
    assert summary["data_freshness"] == {"fresh": 2, "stale": 0, "unknown": 0}
    assert insert_holdings.call_count == 2
    assert insert_non_stock_assets.call_count == 2
    assert insert_scrape_run.call_count == 2
