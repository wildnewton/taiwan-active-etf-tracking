from datetime import date, datetime
from unittest.mock import AsyncMock, patch

import pytest

import db
import pipeline
from models import HoldingRow
from scrapers.official import _validate_official_rows


TARGET_DATE = date(2026, 7, 17)
STALE_DATE = date(2026, 7, 16)
SCRAPED_AT = datetime(2026, 7, 17, 21, 0)
ETF_CODE = "00980A"
STOCKS = [
    ("2301", "光寶科"),
    ("2303", "聯電"),
    ("2308", "台達電"),
    ("2317", "鴻海"),
    ("2330", "台積電"),
]


def _holding_rows(
    data_date=TARGET_DATE,
    *,
    total_weight=90.0,
    source_type="moneydj_primary",
    count=5,
):
    weight = total_weight / count
    return [
        HoldingRow(
            date=data_date,
            etf_code=ETF_CODE,
            asset_name=f"{name}({code}.TW)",
            asset_type="stock",
            stock_code=code,
            stock_name=name,
            shares=1000 + index,
            weight_pct=weight,
            source_url="https://example.test",
            source_type=source_type,
            extraction_method="test",
            scraped_at=SCRAPED_AT,
        )
        for index, (code, name) in enumerate(STOCKS[:count])
    ]


def _result(data_date=TARGET_DATE, *, total_weight=90.0):
    rows = [
        {
            "date": data_date.isoformat(),
            "etf_code": row.etf_code,
            "asset_name": row.asset_name,
            "asset_type": row.asset_type,
            "stock_code": row.stock_code,
            "stock_name": row.stock_name,
            "shares": row.shares,
            "weight_pct": row.weight_pct,
            "source_url": row.source_url,
            "source_type": row.source_type,
            "extraction_method": row.extraction_method,
        }
        for row in _holding_rows(data_date, total_weight=total_weight)
    ]
    return {
        "ok": True,
        "reason": "ok",
        "all_rows": rows,
        "stock_rows": rows,
        "non_stock_rows": [],
        "source_url": "https://example.test",
        "source_type": "moneydj_primary",
        "total_weight_all_rows": total_weight,
        "total_weight_stock_rows": total_weight,
    }


def test_valid_snapshot_does_not_require_weight_near_100(tmp_path):
    db_path = tmp_path / "valid.sqlite"
    db.init_db(str(db_path))
    result = db.replace_daily_snapshot(_holding_rows(total_weight=90.0), [])

    assert result == {"inserted": True, "source_type": "moneydj_primary"}
    assert db.snapshot_exists(TARGET_DATE, ETF_CODE) is True
    assert db.get_canonical_snapshot_source(TARGET_DATE, ETF_CODE) == "moneydj_primary"


def test_persistence_boundary_rejects_structurally_invalid_snapshot(tmp_path):
    db_path = tmp_path / "invalid.sqlite"
    db.init_db(str(db_path))

    result = db.replace_daily_snapshot(_holding_rows(count=4), [])

    assert result == {
        "inserted": False,
        "reason": "invalid_snapshot:fewer_than_5_rows",
    }
    assert db.snapshot_exists(TARGET_DATE, ETF_CODE) is False


def test_official_total_weight_is_not_a_validity_gate():
    rows = _result(total_weight=10.0)["all_rows"]

    assert _validate_official_rows(rows) == (True, "ok")


def test_stale_equivalent_snapshot_is_skipped_with_comparison_evidence(tmp_path):
    db_path = tmp_path / "stale.sqlite"
    db.init_db(str(db_path))
    db.replace_daily_snapshot(_holding_rows(STALE_DATE, total_weight=90.0), [])
    summary = pipeline._new_summary(TARGET_DATE, 1, TARGET_DATE, True)

    pipeline._record_result(
        summary,
        ETF_CODE,
        TARGET_DATE,
        TARGET_DATE,
        _result(STALE_DATE, total_weight=90.5),
    )

    assert summary["skipped_stale_existing"] == 1
    assert summary["stale_existing_comparisons"] == [
        {
            "etf_code": ETF_CODE,
            "target_date": TARGET_DATE.isoformat(),
            "data_date": STALE_DATE.isoformat(),
            "incoming_source_type": "moneydj_primary",
            "existing_source_type": "moneydj_primary",
            "incoming_stock_count": 5,
            "existing_stock_count": 5,
            "incoming_total_weight": 90.5,
            "existing_total_weight": 90.0,
            "weight_delta_pct_points": 0.5,
            "equivalent": True,
            "action": "skip_rewrite",
            "reason": "same_stock_count_and_weight_delta_lt_1",
        }
    ]


@pytest.mark.asyncio
async def test_selected_scrape_skips_existing_snapshot_by_default(tmp_path):
    db_path = tmp_path / "selected.sqlite"
    db.init_db(str(db_path))
    db.replace_daily_snapshot(_holding_rows(), [])
    scraper = AsyncMock(side_effect=AssertionError("existing valid snapshot must be skipped"))

    with patch("pipeline.scrape_holdings_with_browser_async", new=scraper):
        summary = await pipeline.run_selected_scrape_with_browser_async(
            str(db_path),
            [ETF_CODE],
            page=object(),
            run_date=TARGET_DATE,
            target_date=TARGET_DATE,
        )

    scraper.assert_not_awaited()
    assert summary["preexisting_success"] == 1
    assert summary["attempted_etf_codes"] == []


@pytest.mark.asyncio
async def test_selected_scrape_force_true_fetches_existing_snapshot(tmp_path):
    db_path = tmp_path / "forced.sqlite"
    db.init_db(str(db_path))
    db.replace_daily_snapshot(_holding_rows(), [])
    scraper = AsyncMock(return_value=_result())

    with patch("pipeline.scrape_holdings_with_browser_async", new=scraper):
        summary = await pipeline.run_selected_scrape_with_browser_async(
            str(db_path),
            [ETF_CODE],
            page=object(),
            run_date=TARGET_DATE,
            target_date=TARGET_DATE,
            force=True,
        )

    scraper.assert_awaited_once()
    assert summary["preexisting_success"] == 0
    assert summary["attempted_etf_codes"] == [ETF_CODE]
