from datetime import date, datetime
from unittest.mock import AsyncMock, patch

import pytest

import pipeline


@pytest.mark.asyncio
async def test_daily_browser_scrape_uses_target_date_for_default_universe():
    run_at = datetime(
        2026,
        7,
        20,
        14,
        0,
        tzinfo=pipeline.TAIPEI_TIMEZONE,
    )
    target_date = date(2026, 7, 17)
    listing_date = date(2026, 7, 20)

    def active_etfs(as_of_date):
        return [{"code": "NEW"}] if as_of_date >= listing_date else []

    scraper = AsyncMock()
    with patch("pipeline.init_db"), patch(
        "pipeline._current_run_at",
        return_value=run_at,
    ), patch(
        "pipeline._expected_data_date_for_run",
        return_value=target_date,
    ), patch(
        "pipeline._is_trading_day_for_run",
        return_value=True,
    ), patch(
        "pipeline._active_etfs_for_run",
        side_effect=active_etfs,
    ) as active, patch(
        "pipeline.scrape_holdings_with_browser_async",
        new=scraper,
    ):
        summary = await pipeline.run_daily_scrape_with_browser_async(
            ":memory:",
            page=object(),
        )

    active.assert_called_once_with(target_date)
    scraper.assert_not_awaited()
    assert summary["date"] == listing_date.isoformat()
    assert summary["expected_data_date"] == target_date.isoformat()
    assert summary["total_etfs"] == 0
