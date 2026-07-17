from datetime import date, datetime
from unittest.mock import Mock, patch

import db
import pipeline
from models import HoldingRow


RUN_DATE = date(2026, 6, 22)
RUN_AT = datetime(
    2026,
    6,
    22,
    15,
    0,
    tzinfo=pipeline.TAIPEI_TIMEZONE,
)


def _seed_successful_memory_snapshot():
    db.init_db(":memory:")
    db.insert_holdings([
        HoldingRow(
            date=RUN_DATE,
            etf_code="00980A",
            asset_name="台積電(2330.TW)",
            asset_type="stock",
            stock_code="2330",
            stock_name="台積電",
            shares=1000,
            weight_pct=10.0,
            source_url="https://example.test",
            source_type="moneydj_primary",
            extraction_method="test",
            scraped_at=RUN_AT,
        )
    ])



def _success_result():
    row = {
        "date": RUN_DATE.isoformat(),
        "etf_code": "00980A",
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


def test_scrape_unit_path_ignores_leaked_memory_snapshot():
    _seed_successful_memory_snapshot()
    scraper = Mock(return_value=_success_result())

    with patch("pipeline._current_run_at", return_value=RUN_AT), patch(
        "pipeline._active_etfs_for_run", return_value=[{"code": "00980A"}]
    ), patch(
        "pipeline.latest_tw_trading_day_on_or_before", return_value=RUN_DATE
    ), patch(
        "pipeline.is_tw_trading_day", return_value=True
    ), patch(
        "pipeline.init_db"
    ), patch(
        "pipeline.scrape_holdings", scraper
    ), patch(
        "pipeline.replace_daily_snapshot", return_value={"inserted": True}
    ):
        summary = pipeline.run_daily_scrape(":memory:")

    scraper.assert_called_once_with("00980A", RUN_DATE)
    assert summary["preexisting_success"] == 0
    assert summary["moneydj_success"] == 1
