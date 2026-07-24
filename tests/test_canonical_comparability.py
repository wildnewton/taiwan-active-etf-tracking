import sqlite3

import db
from changes import detect_holding_changes


_FILLER_STOCKS = [
    ("9001", "Fixture 9001"),
    ("9002", "Fixture 9002"),
    ("9003", "Fixture 9003"),
    ("9004", "Fixture 9004"),
    ("9005", "Fixture 9005"),
]


def insert_holding(
    date,
    etf_code,
    stock_code,
    stock_name,
    shares,
    weight_pct,
    source_type="moneydj_primary",
    complete=True,
):
    from etf_universe import upsert_etf

    upsert_etf({"code": etf_code, "name": f"ETF {etf_code}", "market": "TWSE"})
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_daily_holdings (
                date, etf_code, asset_name, asset_type, stock_code, stock_name,
                shares, weight_pct, source_url, source_type, extraction_method,
                scraped_at
            ) VALUES (?, ?, ?, 'stock', ?, ?, ?, ?, 'https://example.test',
                ?, 'test', '2026-06-24T00:00:00')
            """,
            (
                date,
                etf_code,
                f"{stock_name}({stock_code}.TW)",
                stock_code,
                stock_name,
                shares,
                weight_pct,
                source_type,
            ),
        )
        if complete:
            existing_codes = {
                row[0]
                for row in conn.execute(
                    """
                    SELECT stock_code FROM etf_daily_holdings
                    WHERE date = ? AND etf_code = ? AND source_type = ?
                    """,
                    (date, etf_code, source_type),
                ).fetchall()
            }
            for filler_code, filler_name in _FILLER_STOCKS:
                if len(existing_codes) >= 5:
                    break
                if filler_code in existing_codes:
                    continue
                conn.execute(
                    """
                    INSERT INTO etf_daily_holdings (
                        date, etf_code, asset_name, asset_type, stock_code,
                        stock_name, shares, weight_pct, source_url, source_type,
                        extraction_method, scraped_at
                    ) VALUES (?, ?, ?, 'stock', ?, ?, 0, 0,
                              'https://example.test', ?, 'test',
                              '2026-06-24T00:00:00')
                    """,
                    (
                        date,
                        etf_code,
                        f"{filler_name}({filler_code}.TW)",
                        filler_code,
                        filler_name,
                        source_type,
                    ),
                )
                existing_codes.add(filler_code)


def fetch_changes(etf_code="00980A", date="2026-06-24"):
    conn = db._connect()
    old_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            """
            SELECT *
            FROM etf_holding_changes
            WHERE date = ? AND etf_code = ?
            ORDER BY stock_code
            """,
            (date, etf_code),
        ).fetchall()
    finally:
        conn.row_factory = old_factory


def fetch_change(stock_code, etf_code="00980A", date="2026-06-24"):
    rows = fetch_changes(etf_code=etf_code, date=date)
    for row in rows:
        if row["stock_code"] == stock_code:
            return row
    return None


def test_change_detection_uses_one_canonical_source_per_etf_date():
    db.init_db(":memory:")

    # MoneyDJ is valid and higher priority. Official is a valid lower-priority
    # duplicate and must not affect ranking or deltas.
    insert_holding("2026-06-23", "00980A", "2330", "台積電", 100, 10.0, "moneydj_primary")
    insert_holding("2026-06-23", "00980A", "2308", "台達電", 200, 5.0, "moneydj_primary")
    insert_holding("2026-06-23", "00980A", "2330", "台積電", None, 8.0, "official_static")

    insert_holding("2026-06-24", "00980A", "2330", "台積電", 110, 12.0, "moneydj_primary")
    insert_holding("2026-06-24", "00980A", "2308", "台達電", 200, 5.0, "moneydj_primary")
    insert_holding("2026-06-24", "00980A", "2330", "台積電", None, 7.5, "official_static")

    summary = detect_holding_changes("2026-06-24", "2026-06-23")

    assert summary["ok"] is True
    assert summary["skipped_etfs"] == []
    assert summary["rows"] == 6

    tsmc = fetch_change("2330")
    assert tsmc["source_type"] == "moneydj_primary"
    assert tsmc["prev_rank"] == 1
    assert tsmc["rank"] == 1
    assert tsmc["prev_shares"] == 100
    assert tsmc["shares"] == 110
    assert tsmc["shares_delta_1d"] == 10
    assert tsmc["position_change_type"] == "confirmed_active_add"


def test_partial_current_source_is_not_comparable_and_does_not_create_removed_positions():
    db.init_db(":memory:")

    # Previous day is a structurally valid holdings source.
    insert_holding("2026-06-23", "00980A", "2330", "台積電", 100, 10.0, "moneydj_primary")
    insert_holding("2026-06-23", "00980A", "2308", "台達電", 100, 8.0, "moneydj_primary")
    insert_holding("2026-06-23", "00980A", "2454", "聯發科", 100, 6.0, "moneydj_primary")
    insert_holding("2026-06-23", "00980A", "2383", "台光電", 100, 4.0, "moneydj_primary")

    # Current day only has one fallback stock and is structurally invalid. It
    # must be skipped, not treated as removed positions.
    insert_holding(
        "2026-06-24",
        "00980A",
        "2330",
        "台積電",
        100,
        10.5,
        "official_static",
        complete=False,
    )

    summary = detect_holding_changes("2026-06-24", "2026-06-23")

    assert summary["ok"] is False
    assert summary["rows"] == 0
    assert summary["new_positions"] == 0
    assert summary["removed_positions"] == 0
    assert summary["skipped_etfs"] == ["00980A"]
    assert "not comparable" in summary["reason"]
    assert fetch_changes() == []


def test_high_overlap_source_switch_is_comparable():
    db.init_db(":memory:")

    insert_holding("2026-06-23", "00980A", "2330", "台積電", 100, 10.0, "moneydj_primary")
    insert_holding("2026-06-23", "00980A", "2308", "台達電", 100, 8.0, "moneydj_primary")
    insert_holding("2026-06-23", "00980A", "2454", "聯發科", 100, 6.0, "moneydj_primary")

    insert_holding("2026-06-24", "00980A", "2330", "台積電", 105, 10.5, "moneydj_browser")
    insert_holding("2026-06-24", "00980A", "2308", "台達電", 100, 8.0, "moneydj_browser")
    insert_holding("2026-06-24", "00980A", "2454", "聯發科", 100, 6.0, "moneydj_browser")

    summary = detect_holding_changes("2026-06-24", "2026-06-23")

    assert summary["ok"] is True
    assert summary["rows"] == 7
    assert summary["skipped_etfs"] == []
    assert fetch_change("2330")["position_change_type"] == "confirmed_active_add"
