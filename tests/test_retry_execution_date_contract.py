from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import pipeline
import retry_stale_scrapes


# A historical holdings target must not replace today's execution identity.
EXECUTION_DATE = date(2026, 7, 17)
TARGET_DATE = date(2026, 7, 10)


def _success_result(etf_code: str, data_date: date) -> dict:
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


@pytest.mark.asyncio
async def test_selected_retry_keeps_execution_date_separate_from_target_date():
    run_at = datetime(
        2026,
        7,
        17,
        21,
        30,
        tzinfo=pipeline.TAIPEI_TIMEZONE,
    )
    page = object()
    scraper = AsyncMock(return_value=_success_result("A", TARGET_DATE))

    with patch("pipeline._current_run_at", return_value=run_at), patch(
        "pipeline.scrape_holdings_with_browser_async",
        new=scraper,
    ), patch("pipeline.init_db"), patch(
        "pipeline.replace_daily_snapshot",
        return_value={"inserted": True},
    ):
        summary = await pipeline.run_selected_scrape_with_browser_async(
            ":memory:",
            ["A"],
            page=page,
            target_date=TARGET_DATE,
        )

    scraper.assert_awaited_once_with("A", page, target_date=TARGET_DATE)
    assert summary["date"] == EXECUTION_DATE.isoformat()
    assert summary["expected_data_date"] == TARGET_DATE.isoformat()
    assert summary["data_freshness"] == {"fresh": 1, "stale": 0, "unknown": 0}


def test_retry_passes_target_without_overwriting_execution_date():
    with patch.object(retry_stale_scrapes.db, "init_db"), patch.object(
        retry_stale_scrapes,
        "get_retry_candidates",
        side_effect=[
            [{"etf_code": "A", "data_date": "2026-07-09"}],
            [],
        ],
    ), patch.object(
        retry_stale_scrapes,
        "run_selected_scrape_with_browser",
        return_value={"date": EXECUTION_DATE.isoformat()},
    ) as selected, patch.object(
        retry_stale_scrapes,
        "detect_holding_changes",
        return_value={"ok": True, "date": TARGET_DATE.isoformat()},
    ), patch.object(
        retry_stale_scrapes,
        "generate_manager_intent_rollups",
        return_value={"ok": True},
    ), patch.object(
        retry_stale_scrapes,
        "generate_manager_signals",
        return_value={"ok": True},
    ), patch.object(
        retry_stale_scrapes,
        "_overwrite_reports",
        return_value={},
    ) as reports:
        retry_stale_scrapes.retry_missing_holdings(
            db_path=":memory:",
            target_date=TARGET_DATE.isoformat(),
            report_dir=Path("reports"),
        )

    selected.assert_called_once_with(
        ":memory:",
        ["A"],
        target_date=TARGET_DATE.isoformat(),
    )
    reports.assert_called_once_with(
        ":memory:",
        TARGET_DATE.isoformat(),
        Path("reports"),
        quality_run_date=EXECUTION_DATE.isoformat(),
    )
