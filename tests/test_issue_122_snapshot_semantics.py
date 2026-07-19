from datetime import date, datetime
import inspect
from pathlib import Path

import db
import pipeline
from models import HoldingRow


DATA_DATE = date(2026, 7, 17)


def _holding(stock_code: str, weight_pct: float, source_type: str = "moneydj_primary") -> HoldingRow:
    return HoldingRow(
        date=DATA_DATE,
        etf_code="00981A",
        asset_name=f"Stock {stock_code}",
        asset_type="stock",
        stock_code=stock_code,
        stock_name=f"Stock {stock_code}",
        shares=1000,
        weight_pct=weight_pct,
        source_url="https://example.test",
        source_type=source_type,
        extraction_method="test",
        scraped_at=datetime(2026, 7, 17, 21, 0),
    )


def _valid_rows(total_weight: float, source_type: str = "moneydj_primary") -> list[HoldingRow]:
    weights = [total_weight / 5.0] * 5
    return [
        _holding(stock_code, weight, source_type)
        for stock_code, weight in zip(
            ["2330", "2317", "2454", "2308", "2881"],
            weights,
            strict=True,
        )
    ]


def test_valid_snapshot_does_not_require_total_weight_near_100():
    db.init_db(":memory:")
    db.insert_holdings(_valid_rows(90.0))

    assert db.snapshot_exists(DATA_DATE, "00981A") is True
    assert db.get_canonical_snapshot_source(DATA_DATE, "00981A") == "moneydj_primary"


def test_snapshot_requires_general_hard_validation():
    db.init_db(":memory:")
    db.insert_holdings(_valid_rows(90.0)[:4])

    assert db.snapshot_exists(DATA_DATE, "00981A") is False
    assert db.get_canonical_snapshot_source(DATA_DATE, "00981A") is None


def test_stale_equivalence_uses_count_and_weight_delta_not_distance_from_100():
    decision = pipeline._compare_snapshot_metrics(
        incoming_stock_count=5,
        incoming_total_weight=90.99,
        existing_stock_count=5,
        existing_total_weight=90.0,
    )
    assert decision["equivalent"] is True
    assert decision["weight_delta_pct_points"] == 0.99

    boundary = pipeline._compare_snapshot_metrics(
        incoming_stock_count=5,
        incoming_total_weight=91.0,
        existing_stock_count=5,
        existing_total_weight=90.0,
    )
    assert boundary["equivalent"] is False

    count_mismatch = pipeline._compare_snapshot_metrics(
        incoming_stock_count=4,
        incoming_total_weight=90.5,
        existing_stock_count=5,
        existing_total_weight=90.0,
    )
    assert count_mismatch["equivalent"] is False


def test_selected_scrape_exposes_explicit_force_flag_and_readme_documents_it():
    signature = inspect.signature(pipeline.run_selected_scrape_with_browser_async)
    assert signature.parameters["force"].default is False

    readme = Path("README.md").read_text(encoding="utf-8")
    assert "force=True" in readme
    assert "forced scrape" in readme.lower()
