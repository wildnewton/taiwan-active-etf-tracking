from datetime import date, datetime
from unittest.mock import AsyncMock, Mock, call, patch

import pytest

import pipeline


RUN_DATE = date(2026, 6, 23)
PREVIOUS_TRADING_DATE = date(2026, 6, 22)
RUN_AT_BEFORE_CUTOFF = datetime(
    2026,
    6,
    23,
    14,
    0,
    tzinfo=pipeline.TAIPEI_TIMEZONE,
)
RUN_AT_AFTER_CUTOFF = datetime(
    2026,
    6,
    23,
    15,
    0,
    tzinfo=pipeline.TAIPEI_TIMEZONE,
)


def make_success(etf_code: str, row_date: date = RUN_DATE) -> dict:
    row = {
        "date": row_date.isoformat(),
        "etf_code": etf_code,
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


def test_sync_precheck_uses_expected_data_date_and_persists_skip_observation():
    captured_runs = []
    scrape_fn = Mock(side_effect=AssertionError("scraper must not run"))

    with patch(
        "pipeline.latest_tw_trading_day_on_or_before",
        return_value=PREVIOUS_TRADING_DATE,
    ), patch("pipeline.is_tw_trading_day", return_value=True), patch(
        "pipeline.snapshot_exists",
        return_value=True,
    ) as snapshot_exists, patch(
        "pipeline.replace_daily_snapshot",
    ) as replace_daily_snapshot, patch(
        "pipeline.insert_scrape_run",
        side_effect=captured_runs.append,
    ):
        summary = pipeline._run_scrape_sync(
            ":memory:",
            [{"code": "00980A"}],
            scrape_fn,
            already_initialized=True,
            run_at=RUN_AT_BEFORE_CUTOFF,
        )

    snapshot_exists.assert_called_once_with(PREVIOUS_TRADING_DATE, "00980A")
    scrape_fn.assert_not_called()
    replace_daily_snapshot.assert_not_called()
    assert summary["expected_data_date"] == "2026-06-22"
    assert summary["skipped_existing_snapshot"] == 1
    assert summary["existing_snapshot_etfs"] == [
        {
            "etf_code": "00980A",
            "data_date": "2026-06-22",
            "reason": "expected_snapshot_already_exists",
        }
    ]
    assert summary["moneydj_success"] == 0
    assert summary["official_success"] == 0
    assert summary["failed"] == 0

    assert len(captured_runs) == 1
    observation = captured_runs[0]
    assert observation.status == "skipped_existing_snapshot"
    assert observation.data_date == PREVIOUS_TRADING_DATE
    assert observation.error == "expected_snapshot_already_exists"
    assert observation.primary_source == "none"
    assert observation.primary_success is False
    assert observation.moneydj_browser_used is False
    assert observation.official_fallback_used is False
    assert observation.rows_extracted == 0


@pytest.mark.asyncio
async def test_async_nightly_precheck_does_not_await_scraper():
    captured_runs = []
    scrape_fn = AsyncMock(side_effect=AssertionError("scraper must not run"))

    with patch("pipeline.init_db"), patch(
        "pipeline.snapshot_exists",
        return_value=True,
    ) as snapshot_exists, patch(
        "pipeline.replace_daily_snapshot",
    ) as replace_daily_snapshot, patch(
        "pipeline.insert_scrape_run",
        side_effect=captured_runs.append,
    ):
        summary = await pipeline._run_scrape_async(
            ":memory:",
            [{"code": "00980A"}],
            scrape_fn,
            run_date=RUN_DATE,
            use_trading_calendar=False,
        )

    snapshot_exists.assert_called_once_with(RUN_DATE, "00980A")
    scrape_fn.assert_not_awaited()
    replace_daily_snapshot.assert_not_called()
    assert summary["skipped_existing_snapshot"] == 1
    assert captured_runs[0].status == "skipped_existing_snapshot"


def test_mixed_universe_skips_exact_snapshot_and_scrapes_missing_snapshot():
    captured_runs = []
    scrape_fn = Mock(side_effect=lambda etf_code, _: make_success(etf_code))

    with patch(
        "pipeline.snapshot_exists",
        side_effect=lambda data_date, etf_code: data_date == RUN_DATE and etf_code == "00980A",
    ) as snapshot_exists, patch(
        "pipeline.replace_daily_snapshot",
        return_value={"inserted": True},
    ) as replace_daily_snapshot, patch(
        "pipeline.insert_scrape_run",
        side_effect=captured_runs.append,
    ):
        summary = pipeline._run_scrape_sync(
            ":memory:",
            [{"code": "00980A"}, {"code": "00981A"}],
            scrape_fn,
            already_initialized=True,
            use_trading_calendar=False,
            run_at=RUN_AT_AFTER_CUTOFF,
        )

    assert snapshot_exists.call_args_list == [
        call(RUN_DATE, "00980A"),
        call(RUN_DATE, "00981A"),
    ]
    scrape_fn.assert_called_once_with("00981A", RUN_DATE)
    replace_daily_snapshot.assert_called_once()
    assert summary["total_etfs"] == 2
    assert summary["skipped_existing_snapshot"] == 1
    assert summary["moneydj_success"] == 1
    assert summary["official_success"] == 0
    assert summary["failed"] == 0
    assert [item.status for item in captured_runs] == [
        "skipped_existing_snapshot",
        "success",
    ]


@pytest.mark.asyncio
async def test_selected_manual_scrape_bypasses_existing_snapshot_precheck():
    page = object()
    browser_scrape = AsyncMock(return_value=make_success("00980A"))

    with patch("pipeline.init_db"), patch(
        "pipeline.snapshot_exists",
        return_value=True,
    ) as snapshot_exists, patch(
        "pipeline.scrape_holdings_with_browser_async",
        new=browser_scrape,
    ), patch(
        "pipeline.replace_daily_snapshot",
        return_value={"inserted": True},
    ), patch("pipeline.insert_scrape_run"):
        summary = await pipeline.run_selected_scrape_with_browser_async(
            ":memory:",
            ["00980A"],
            page=page,
            run_date=RUN_DATE,
        )

    snapshot_exists.assert_not_called()
    browser_scrape.assert_awaited_once_with(
        "00980A",
        page,
        target_date=RUN_DATE,
    )
    assert summary["skipped_existing_snapshot"] == 0
    assert summary["moneydj_success"] == 1


def test_only_older_snapshot_does_not_skip_expected_date_scrape():
    scrape_fn = Mock(return_value=make_success("00980A"))

    with patch(
        "pipeline.snapshot_exists",
        return_value=False,
    ) as snapshot_exists, patch(
        "pipeline.replace_daily_snapshot",
        return_value={"inserted": True},
    ), patch("pipeline.insert_scrape_run"):
        summary = pipeline._run_scrape_sync(
            ":memory:",
            [{"code": "00980A"}],
            scrape_fn,
            already_initialized=True,
            use_trading_calendar=False,
            run_at=RUN_AT_AFTER_CUTOFF,
        )

    snapshot_exists.assert_called_once_with(RUN_DATE, "00980A")
    scrape_fn.assert_called_once_with("00980A", RUN_DATE)
    assert summary["skipped_existing_snapshot"] == 0
    assert summary["moneydj_success"] == 1
