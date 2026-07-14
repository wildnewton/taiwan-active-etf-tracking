from datetime import date, datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest

import db
import pipeline
from models import HoldingRow, ScrapeRun


RUN_DATE = date(2026, 7, 14)
STALE_DATE = date(2026, 7, 13)
RUN_AT = datetime(
    2026,
    7,
    14,
    15,
    0,
    tzinfo=pipeline.TAIPEI_TIMEZONE,
)


def _holding(etf_code: str, data_date: date = RUN_DATE) -> HoldingRow:
    return HoldingRow(
        date=data_date,
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
        scraped_at=datetime(2026, 7, 14, 15, 0),
    )


def _scrape_run(
    etf_code: str,
    *,
    scrape_date: date = RUN_DATE,
    data_date: date = RUN_DATE,
) -> ScrapeRun:
    return ScrapeRun(
        date=scrape_date,
        data_date=data_date,
        etf_code=etf_code,
        status="success",
        primary_source="moneydj_primary",
        primary_success=True,
        moneydj_browser_used=False,
        official_fallback_used=False,
        official_success=False,
        rows_extracted=1,
        stock_rows_extracted=1,
        non_stock_rows_extracted=0,
        total_weight_all_rows=10.0,
        total_weight_stock_rows=10.0,
        source_url="https://example.test",
        error=None,
        started_at=datetime(2026, 7, 14, 15, 0),
        finished_at=datetime(2026, 7, 14, 15, 1),
    )


def _make_success(etf_code: str, data_date: date = RUN_DATE) -> dict:
    row = {
        "date": data_date.isoformat(),
        "etf_code": etf_code,
        "asset_name": "台積電(2330.TW)",
        "asset_type": "stock",
        "stock_code": "2330",
        "stock_name": "台積電",
        "shares": 1000,
        "weight_pct": 10.0,
        "source_url": "https://example.test",
        "source_type": "moneydj_primary",
        "extraction_method": "test",
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


def _seed_snapshot(db_path, etf_code: str, *, run_data_date: date = RUN_DATE) -> None:
    db.init_db(str(db_path))
    db.insert_holdings([_holding(etf_code)])
    db.insert_scrape_run(_scrape_run(etf_code, data_date=run_data_date))


def _read_scrape_run(db_path, etf_code: str):
    db.init_db(str(db_path))
    with db._connect() as conn:
        return conn.execute(
            """
            SELECT status, data_date, rows_extracted, error
            FROM etf_scrape_runs
            WHERE date = ? AND etf_code = ?
            """,
            (RUN_DATE.isoformat(), etf_code),
        ).fetchone()


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeBrowserStack:
    def __init__(self):
        self.page = object()
        self.context = Mock()
        self.context.new_page = AsyncMock(return_value=self.page)
        self.context.close = AsyncMock()
        self.browser = Mock()
        self.browser.new_context = AsyncMock(return_value=self.context)
        self.browser.close = AsyncMock()
        self.playwright = Mock()
        self.playwright.chromium.launch = AsyncMock(return_value=self.browser)
        self.async_playwright = Mock(
            return_value=_AsyncContext(self.playwright)
        )


def test_validated_exact_snapshot_skips_without_writing_a_skip_run(tmp_path):
    db_path = tmp_path / "validated.sqlite"
    _seed_snapshot(db_path, "00980A")
    scraper = Mock(side_effect=AssertionError("scraper must not run"))

    with patch("pipeline.insert_scrape_run", wraps=db.insert_scrape_run) as insert_run:
        summary = pipeline._run_scrape_sync(
            str(db_path),
            [{"code": "00980A"}],
            scraper,
            already_initialized=True,
            use_trading_calendar=False,
            run_at=RUN_AT,
        )

    scraper.assert_not_called()
    insert_run.assert_not_called()
    assert _read_scrape_run(db_path, "00980A") == (
        "success",
        RUN_DATE.isoformat(),
        1,
        None,
    )
    assert summary["preexisting_success"] == 1
    assert summary["moneydj_success"] == 0
    assert summary["official_success"] == 0
    assert summary["failed"] == 0
    assert summary["data_freshness"] == {"fresh": 1, "stale": 0, "unknown": 0}
    assert summary["data_date_min"] == RUN_DATE.isoformat()
    assert summary["data_date_max"] == RUN_DATE.isoformat()
    assert "skipped_existing_snapshot" not in summary
    assert "existing_snapshot_etfs" not in summary


def test_snapshot_without_success_record_does_not_suppress_scrape(tmp_path):
    db_path = tmp_path / "snapshot-only.sqlite"
    db.init_db(str(db_path))
    db.insert_holdings([_holding("00980A")])
    scraper = Mock(return_value=_make_success("00980A"))

    summary = pipeline._run_scrape_sync(
        str(db_path),
        [{"code": "00980A"}],
        scraper,
        already_initialized=True,
        use_trading_calendar=False,
        run_at=RUN_AT,
    )

    scraper.assert_called_once_with("00980A", RUN_DATE)
    assert summary["preexisting_success"] == 0
    assert summary["moneydj_success"] == 1


def test_success_record_without_snapshot_does_not_suppress_scrape(tmp_path):
    db_path = tmp_path / "success-only.sqlite"
    db.init_db(str(db_path))
    db.insert_scrape_run(_scrape_run("00980A"))
    scraper = Mock(return_value=_make_success("00980A"))

    summary = pipeline._run_scrape_sync(
        str(db_path),
        [{"code": "00980A"}],
        scraper,
        already_initialized=True,
        use_trading_calendar=False,
        run_at=RUN_AT,
    )

    scraper.assert_called_once_with("00980A", RUN_DATE)
    assert summary["preexisting_success"] == 0
    assert summary["moneydj_success"] == 1


def test_stale_success_record_does_not_validate_exact_snapshot(tmp_path):
    db_path = tmp_path / "stale-success.sqlite"
    _seed_snapshot(db_path, "00980A", run_data_date=STALE_DATE)
    scraper = Mock(return_value=_make_success("00980A"))

    summary = pipeline._run_scrape_sync(
        str(db_path),
        [{"code": "00980A"}],
        scraper,
        already_initialized=True,
        use_trading_calendar=False,
        run_at=RUN_AT,
    )

    scraper.assert_called_once_with("00980A", RUN_DATE)
    assert summary["preexisting_success"] == 0
    assert summary["moneydj_success"] == 1


@pytest.mark.asyncio
async def test_all_complete_daily_browser_run_returns_before_playwright(tmp_path):
    db_path = tmp_path / "all-complete.sqlite"
    _seed_snapshot(db_path, "00980A")

    with patch("pipeline._current_run_at", return_value=RUN_AT), patch(
        "pipeline._active_etfs_for_run", return_value=[{"code": "00980A"}]
    ), patch(
        "pipeline.latest_tw_trading_day_on_or_before", return_value=RUN_DATE
    ), patch(
        "pipeline.is_tw_trading_day", return_value=True
    ), patch(
        "playwright.async_api.async_playwright",
        side_effect=AssertionError("Playwright must not start"),
    ) as async_playwright:
        summary = await pipeline.run_daily_scrape_with_browser_async(str(db_path))

    async_playwright.assert_not_called()
    assert summary["preexisting_success"] == 1
    assert summary["data_freshness"]["fresh"] == 1


@pytest.mark.asyncio
async def test_mixed_daily_browser_run_starts_once_and_scrapes_only_missing_etfs(tmp_path):
    db_path = tmp_path / "mixed.sqlite"
    _seed_snapshot(db_path, "00980A")
    browser_stack = _FakeBrowserStack()
    scraper = AsyncMock(
        side_effect=lambda etf_code, page, target_date: _make_success(
            etf_code, target_date
        )
    )

    with patch("pipeline._current_run_at", return_value=RUN_AT), patch(
        "pipeline._active_etfs_for_run",
        return_value=[{"code": "00980A"}, {"code": "00981A"}],
    ), patch(
        "pipeline.latest_tw_trading_day_on_or_before", return_value=RUN_DATE
    ), patch(
        "pipeline.is_tw_trading_day", return_value=True
    ), patch(
        "playwright.async_api.async_playwright",
        new=browser_stack.async_playwright,
    ), patch(
        "pipeline.scrape_holdings_with_browser_async",
        new=scraper,
    ), patch(
        "pipeline.insert_scrape_run", wraps=db.insert_scrape_run
    ) as insert_run:
        summary = await pipeline.run_daily_scrape_with_browser_async(str(db_path))

    browser_stack.async_playwright.assert_called_once_with()
    browser_stack.playwright.chromium.launch.assert_awaited_once_with(headless=True)
    scraper.assert_awaited_once_with(
        "00981A",
        browser_stack.page,
        target_date=RUN_DATE,
    )
    assert insert_run.call_count == 1
    assert summary["total_etfs"] == 2
    assert summary["preexisting_success"] == 1
    assert summary["moneydj_success"] == 1
    assert summary["official_success"] == 0
    assert summary["failed"] == 0
    assert summary["data_freshness"] == {"fresh": 2, "stale": 0, "unknown": 0}
    assert summary["data_date_min"] == RUN_DATE.isoformat()
    assert summary["data_date_max"] == RUN_DATE.isoformat()


@pytest.mark.asyncio
async def test_selected_internal_browser_still_forces_scrape(tmp_path):
    db_path = tmp_path / "selected.sqlite"
    _seed_snapshot(db_path, "00980A")
    browser_stack = _FakeBrowserStack()
    scraper = AsyncMock(return_value=_make_success("00980A"))

    with patch(
        "playwright.async_api.async_playwright",
        new=browser_stack.async_playwright,
    ), patch(
        "pipeline.scrape_holdings_with_browser_async",
        new=scraper,
    ):
        summary = await pipeline.run_selected_scrape_with_browser_async(
            str(db_path),
            ["00980A"],
            run_date=RUN_DATE,
        )

    browser_stack.playwright.chromium.launch.assert_awaited_once_with(headless=True)
    scraper.assert_awaited_once_with(
        "00980A",
        browser_stack.page,
        target_date=RUN_DATE,
    )
    assert summary["preexisting_success"] == 0
    assert summary["moneydj_success"] == 1
