import importlib.util
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import db
import pipeline
from models import HoldingRow


SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "nightly_pipeline.py"
RUN_DATE = date(2026, 7, 14)
RUN_AT = datetime(
    2026,
    7,
    14,
    15,
    0,
    tzinfo=pipeline.TAIPEI_TIMEZONE,
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "nightly_try_run_preexisting_test",
        str(SCRIPT),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed_validated_snapshot(db_path: Path):
    db.init_db(str(db_path))
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



def test_complete_try_run_avoids_playwright_and_preserves_production_db(tmp_path):
    module = _load_module()
    production_db = tmp_path / "production.sqlite"
    production_reports = tmp_path / "reports"
    _seed_validated_snapshot(production_db)
    before_bytes = production_db.read_bytes()

    with patch("pipeline._current_run_at", return_value=RUN_AT), patch(
        "pipeline._active_etfs_for_run", return_value=[{"code": "00980A"}]
    ), patch(
        "pipeline.latest_tw_trading_day_on_or_before", return_value=RUN_DATE
    ), patch(
        "pipeline.is_tw_trading_day", return_value=True
    ), patch(
        "playwright.async_api.async_playwright",
        side_effect=AssertionError("Playwright must not start"),
    ) as async_playwright, patch.object(
        module,
        "get_latest_valid_date",
        return_value=RUN_DATE.isoformat(),
    ), patch.object(
        module,
        "detect_holding_changes",
        return_value={
            "ok": True,
            "date": RUN_DATE.isoformat(),
            "previous_date": "2026-07-13",
            "rows": 1,
            "skipped_etfs": [],
        },
    ), patch.object(
        module, "generate_manager_intent_rollups", return_value={}
    ), patch.object(
        module, "generate_manager_signals", return_value={}
    ), patch.object(
        module, "generate_signal_report", return_value="report"
    ), patch.object(
        module, "generate_traction_report", return_value="traction"
    ):
        result = module.run_try_run(
            str(production_db),
            str(production_reports),
            skip_discovery=True,
        )

    async_playwright.assert_not_called()
    assert result["scrape_summary"]["preexisting_success"] == 1
    assert result["scrape_summary"]["moneydj_success"] == 0
    assert result["scrape_summary"]["data_freshness"] == {
        "fresh": 1,
        "stale": 0,
        "unknown": 0,
    }
    assert production_db.read_bytes() == before_bytes
    assert not production_reports.exists()
