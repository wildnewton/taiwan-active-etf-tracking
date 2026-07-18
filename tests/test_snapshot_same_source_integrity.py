from datetime import date, datetime

import db
from models import HoldingRow


DATA_DATE = date(2026, 7, 15)


def _holding(weight_pct: float, stock_code: str = "2330") -> HoldingRow:
    return HoldingRow(
        date=DATA_DATE,
        etf_code="A",
        asset_name=f"Stock {stock_code}",
        asset_type="stock",
        stock_code=stock_code,
        stock_name=f"Stock {stock_code}",
        shares=100.0,
        weight_pct=weight_pct,
        source_url="https://example.test",
        source_type="moneydj_primary",
        extraction_method="test",
        scraped_at=datetime(2026, 7, 15, 21, 0),
    )


def test_same_source_incomplete_rerun_does_not_replace_complete_snapshot():
    db.init_db(":memory:")
    db.replace_daily_snapshot([_holding(100.0)], [])

    result = db.replace_daily_snapshot([_holding(50.0)], [])

    assert result == {
        "inserted": False,
        "reason": "existing_complete_snapshot_preserved",
        "preserved_source_type": "moneydj_primary",
        "incoming_source_type": "moneydj_primary",
    }
    assert db.snapshot_exists(DATA_DATE, "A") is True
    with db._connect() as conn:
        rows = conn.execute(
            "SELECT stock_code, weight_pct FROM etf_daily_holdings "
            "WHERE date = ? AND etf_code = ?",
            (DATA_DATE.isoformat(), "A"),
        ).fetchall()
    assert rows == [("2330", 100.0)]


def test_same_source_complete_rerun_can_replace_complete_snapshot():
    db.init_db(":memory:")
    db.replace_daily_snapshot([_holding(100.0, "2330")], [])

    result = db.replace_daily_snapshot([_holding(100.0, "2317")], [])

    assert result == {"inserted": True, "source_type": "moneydj_primary"}
    with db._connect() as conn:
        rows = conn.execute(
            "SELECT stock_code, weight_pct FROM etf_daily_holdings "
            "WHERE date = ? AND etf_code = ?",
            (DATA_DATE.isoformat(), "A"),
        ).fetchall()
    assert rows == [("2317", 100.0)]
