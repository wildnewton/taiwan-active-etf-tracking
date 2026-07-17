from datetime import date, datetime
from unittest.mock import patch

import db
import pipeline
from models import HoldingRow, NonStockAssetRow
from pipeline import run_daily_scrape


RUN_DATE = date(2026, 7, 8)


class FixedDate(date):
    @classmethod
    def today(cls):
        return cls(RUN_DATE.year, RUN_DATE.month, RUN_DATE.day)


def holding(
    etf_code="00981A",
    stock_code="2330",
    source_type="moneydj_primary",
    row_date=RUN_DATE,
    shares=1000,
    weight_pct=10.0,
):
    return HoldingRow(
        date=row_date,
        etf_code=etf_code,
        asset_name=f"股票{stock_code}({stock_code}.TW)",
        asset_type="stock",
        stock_code=stock_code,
        stock_name=f"股票{stock_code}",
        shares=shares,
        weight_pct=weight_pct,
        source_url="https://example.test",
        source_type=source_type,
        extraction_method="test",
        scraped_at=datetime(2026, 7, 8, 16, 0),
    )


def non_stock(
    etf_code="00981A",
    asset_name="現金",
    source_type="moneydj_primary",
    row_date=RUN_DATE,
    weight_pct=5.0,
):
    return NonStockAssetRow(
        date=row_date,
        etf_code=etf_code,
        asset_name=asset_name,
        asset_type="cash",
        weight_pct=weight_pct,
        source_url="https://example.test",
        source_type=source_type,
        extraction_method="test",
        scraped_at=datetime(2026, 7, 8, 16, 0),
    )


def scrape_result(etf_code="00981A", source_type="moneydj_primary"):
    stock = {
        "date": "2026/07/08",
        "etf_code": etf_code,
        "asset_name": "股票2330(2330.TW)",
        "asset_type": "stock",
        "stock_code": "2330",
        "stock_name": "股票2330",
        "shares": 1000,
        "weight_pct": 10.0,
        "source_url": "https://example.test",
        "source_type": source_type,
        "extraction_method": "test",
    }
    return {
        "ok": True,
        "reason": "ok",
        "all_rows": [stock],
        "stock_rows": [stock],
        "non_stock_rows": [],
        "source_url": "https://example.test",
        "source_type": source_type,
        "total_weight_all_rows": 10.0,
        "total_weight_stock_rows": 10.0,
    }


def rows_for(etf_code="00981A"):
    with db._connect() as conn:
        return conn.execute(
            """
            SELECT date, etf_code, stock_code, source_type
            FROM etf_daily_holdings
            WHERE etf_code = ?
            ORDER BY date, stock_code, source_type
            """,
            (etf_code,),
        ).fetchall()


def non_stock_rows_for(etf_code="00981A"):
    with db._connect() as conn:
        return conn.execute(
            """
            SELECT date, etf_code, asset_name, source_type
            FROM etf_daily_non_stock_assets
            WHERE etf_code = ?
            ORDER BY date, asset_name, source_type
            """,
            (etf_code,),
        ).fetchall()


def test_moneydj_primary_replaces_existing_official_fallback_snapshot():
    db.init_db(":memory:")
    db.insert_holdings([
        holding(stock_code="2330", source_type="official_fallback"),
        holding(stock_code="2317", source_type="official_fallback"),
    ])

    db.replace_daily_snapshot(
        [holding(stock_code="2330", source_type="moneydj_primary")],
        [],
    )

    assert rows_for() == [("2026-07-08", "00981A", "2330", "moneydj_primary")]


def test_same_source_rerun_replaces_whole_snapshot_and_removes_disappeared_stocks():
    db.init_db(":memory:")
    db.insert_holdings([
        holding(stock_code="2330", source_type="moneydj_primary"),
        holding(stock_code="2317", source_type="moneydj_primary"),
    ])

    db.replace_daily_snapshot(
        [holding(stock_code="2330", source_type="moneydj_primary", shares=2000)],
        [],
    )

    assert rows_for() == [("2026-07-08", "00981A", "2330", "moneydj_primary")]


def test_lower_priority_incoming_source_does_not_overwrite_higher_priority_existing_snapshot():
    db.init_db(":memory:")
    db.insert_holdings([holding(stock_code="2330", source_type="moneydj_primary")])

    db.replace_daily_snapshot(
        [holding(stock_code="2330", source_type="official_fallback")],
        [],
    )

    assert rows_for() == [("2026-07-08", "00981A", "2330", "moneydj_primary")]


def test_complete_lower_priority_snapshot_replaces_incomplete_higher_priority_snapshot():
    db.init_db(":memory:")
    db.insert_holdings([
        holding(
            stock_code="2330",
            source_type="moneydj_primary",
            weight_pct=50.0,
        )
    ])

    result = db.replace_daily_snapshot(
        [
            holding(
                stock_code="2330",
                source_type="official_fallback",
                weight_pct=100.0,
            )
        ],
        [],
    )

    assert result == {"inserted": True, "source_type": "official_fallback"}
    assert rows_for() == [
        ("2026-07-08", "00981A", "2330", "official_fallback")
    ]


def test_complete_higher_priority_snapshot_still_beats_complete_lower_priority_snapshot():
    db.init_db(":memory:")
    db.insert_holdings([
        holding(
            stock_code="2330",
            source_type="moneydj_primary",
            weight_pct=100.0,
        )
    ])

    result = db.replace_daily_snapshot(
        [
            holding(
                stock_code="2330",
                source_type="official_fallback",
                weight_pct=100.0,
            )
        ],
        [],
    )

    assert result["inserted"] is False
    assert result["reason"] == "existing_higher_priority_source_preserved"
    assert rows_for() == [
        ("2026-07-08", "00981A", "2330", "moneydj_primary")
    ]


def test_non_stock_assets_are_replaced_atomically_with_stock_snapshot():
    db.init_db(":memory:")
    db.insert_holdings([holding(stock_code="2330", source_type="official_fallback")])
    db.insert_non_stock_assets([non_stock(asset_name="現金", source_type="official_fallback")])

    db.replace_daily_snapshot(
        [holding(stock_code="2330", source_type="moneydj_primary")],
        [non_stock(asset_name="期貨保證金", source_type="moneydj_primary")],
    )

    assert rows_for() == [("2026-07-08", "00981A", "2330", "moneydj_primary")]
    assert non_stock_rows_for() == [("2026-07-08", "00981A", "期貨保證金", "moneydj_primary")]


def test_snapshot_replacement_is_scoped_to_same_date_and_etf_only():
    db.init_db(":memory:")
    db.insert_holdings([
        holding(etf_code="00981A", stock_code="2330", source_type="official_fallback"),
        holding(etf_code="00982A", stock_code="2317", source_type="official_fallback"),
        holding(etf_code="00981A", stock_code="2382", source_type="official_fallback", row_date=date(2026, 7, 7)),
    ])

    db.replace_daily_snapshot(
        [holding(etf_code="00981A", stock_code="2330", source_type="moneydj_primary")],
        [],
    )

    assert rows_for("00981A") == [
        ("2026-07-07", "00981A", "2382", "official_fallback"),
        ("2026-07-08", "00981A", "2330", "moneydj_primary"),
    ]
    assert rows_for("00982A") == [("2026-07-08", "00982A", "2317", "official_fallback")]


def test_pipeline_success_path_uses_snapshot_replacement_once_per_etf():
    with patch("pipeline.date", FixedDate), \
        patch("pipeline._current_run_at", return_value=datetime.combine(
            FixedDate.today(),
            pipeline.DATA_AVAILABILITY_CUTOFF,
            tzinfo=pipeline.TAIPEI_TIMEZONE,
        )), \
        patch("pipeline._active_etfs_for_run", return_value=[{"code": "00981A"}]), \
        patch("pipeline.scrape_holdings", return_value=scrape_result()), \
        patch("pipeline.init_db"), \
        patch("pipeline.replace_daily_snapshot") as replace_daily_snapshot:
        run_daily_scrape(":memory:")

    replace_daily_snapshot.assert_called_once()


def test_official_fallback_has_explicit_source_priority():
    from source_priority import SOURCE_PRIORITIES, source_priority

    assert "official_fallback" in SOURCE_PRIORITIES
    assert source_priority("moneydj_primary") > source_priority("moneydj_browser")
    assert source_priority("moneydj_browser") > source_priority("official_fallback")
    assert source_priority("official_fallback") > source_priority("unknown_source")
