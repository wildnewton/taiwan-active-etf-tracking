from datetime import date, datetime

import db
from models import HoldingRow


DATA_DATE = date(2026, 7, 15)
STOCKS = [
    ("2301", "Lite-On"),
    ("2303", "UMC"),
    ("2308", "Delta"),
    ("2317", "Hon Hai"),
    ("2330", "TSMC"),
    ("2382", "Quanta"),
]


def _holdings(*, count: int = 5, replaced_code: str | None = None) -> list[HoldingRow]:
    stocks = list(STOCKS[:count])
    if replaced_code is not None:
        stocks[-1] = (replaced_code, f"Stock {replaced_code}")
    return [
        HoldingRow(
            date=DATA_DATE,
            etf_code="A",
            asset_name=f"{name}({code}.TW)",
            asset_type="stock",
            stock_code=code,
            stock_name=name,
            shares=100.0 + index,
            weight_pct=18.0,
            source_url="https://example.test",
            source_type="moneydj_primary",
            extraction_method="test",
            scraped_at=datetime(2026, 7, 15, 21, 0),
        )
        for index, (code, name) in enumerate(stocks)
    ]


def _stored_codes() -> list[str]:
    with db._connect() as conn:
        return [
            row[0]
            for row in conn.execute(
                "SELECT stock_code FROM etf_daily_holdings "
                "WHERE date = ? AND etf_code = ? ORDER BY stock_code",
                (DATA_DATE.isoformat(), "A"),
            ).fetchall()
        ]


def test_same_source_structurally_invalid_rerun_is_rejected():
    db.init_db(":memory:")
    assert db.replace_daily_snapshot(_holdings(count=5), [])["inserted"] is True

    result = db.replace_daily_snapshot(_holdings(count=4), [])

    assert result == {
        "inserted": False,
        "reason": "invalid_snapshot:fewer_than_5_rows",
    }
    assert db.snapshot_exists(DATA_DATE, "A") is True
    assert _stored_codes() == sorted(code for code, _ in STOCKS[:5])


def test_same_source_valid_rerun_replaces_whole_snapshot():
    db.init_db(":memory:")
    assert db.replace_daily_snapshot(_holdings(count=5), [])["inserted"] is True

    result = db.replace_daily_snapshot(
        _holdings(count=5, replaced_code="2454"),
        [],
    )

    assert result == {"inserted": True, "source_type": "moneydj_primary"}
    assert _stored_codes() == sorted([
        "2301",
        "2303",
        "2308",
        "2317",
        "2454",
    ])


def test_same_source_valid_rerun_can_remove_a_stock():
    db.init_db(":memory:")
    assert db.replace_daily_snapshot(_holdings(count=6), [])["inserted"] is True

    result = db.replace_daily_snapshot(_holdings(count=5), [])

    assert result == {"inserted": True, "source_type": "moneydj_primary"}
    assert _stored_codes() == sorted(code for code, _ in STOCKS[:5])
