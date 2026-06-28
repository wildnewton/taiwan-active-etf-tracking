import sqlite3

import db
from changes import detect_holding_changes


def insert_holding(date, etf_code, stock_code, stock_name, shares, weight_pct, source_type="moneydj_primary"):
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
            (date, etf_code, f"{stock_name}({stock_code}.TW)", stock_code, stock_name, shares, weight_pct, source_type),
        )


def fetch_diagnostics(date="2026-06-24", prev_date="2026-06-23"):
    conn = db._connect()
    old_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            """
            SELECT *
            FROM etf_change_diagnostics
            WHERE date = ? AND prev_date = ?
            ORDER BY etf_code
            """,
            (date, prev_date),
        ).fetchall()
    finally:
        conn.row_factory = old_factory


def test_db_initializes_change_diagnostics_table():
    db.init_db(":memory:")
    with db._connect() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(etf_change_diagnostics)").fetchall()}
    assert {
        "date", "prev_date", "etf_code", "status", "reason",
        "current_source_type", "previous_source_type",
        "current_stock_count", "previous_stock_count",
        "overlap_ratio", "size_ratio", "created_at",
    }.issubset(columns)


def test_comparable_etf_persists_included_diagnostic():
    db.init_db(":memory:")
    insert_holding("2026-06-23", "00980A", "2330", "TSMC", 100, 10.0)
    insert_holding("2026-06-23", "00980A", "2308", "Delta", 100, 8.0)
    insert_holding("2026-06-24", "00980A", "2330", "TSMC", 110, 11.0)
    insert_holding("2026-06-24", "00980A", "2308", "Delta", 100, 8.0)

    summary = detect_holding_changes("2026-06-24", "2026-06-23")

    assert summary["ok"] is True
    rows = fetch_diagnostics()
    assert len(rows) == 1
    row = rows[0]
    assert row["etf_code"] == "00980A"
    assert row["status"] == "included"
    assert row["reason"] == "comparable_source_pair"
    assert row["current_source_type"] == "moneydj_primary"
    assert row["previous_source_type"] == "moneydj_primary"
    assert row["current_stock_count"] == 2
    assert row["previous_stock_count"] == 2
    assert row["overlap_ratio"] == 1.0
    assert row["size_ratio"] == 1.0


def test_skipped_etfs_persist_specific_reasons():
    db.init_db(":memory:")
    insert_holding("2026-06-23", "00980A", "2330", "TSMC", 100, 10.0)
    insert_holding("2026-06-23", "00980A", "2308", "Delta", 100, 8.0)
    insert_holding("2026-06-23", "00980A", "2454", "MTK", 100, 6.0)
    insert_holding("2026-06-23", "00981A", "2330", "TSMC", 100, 10.0)
    insert_holding("2026-06-24", "00980A", "2330", "TSMC", 100, 10.5, "official_static")
    insert_holding("2026-06-24", "00982A", "2330", "TSMC", 105, 10.5)

    summary = detect_holding_changes("2026-06-24", "2026-06-23")

    assert summary["skipped_etfs"] == ["00980A", "00981A", "00982A"]
    reasons = {row["etf_code"]: row["reason"] for row in fetch_diagnostics()}
    assert reasons == {
        "00980A": "incompatible_source_pair",
        "00981A": "missing_current_source",
        "00982A": "missing_previous_source",
    }


def test_diagnostics_are_replaced_for_same_date_pair_on_rerun():
    db.init_db(":memory:")
    insert_holding("2026-06-23", "00980A", "2330", "TSMC", 100, 10.0)
    insert_holding("2026-06-24", "00980A", "2330", "TSMC", 110, 11.0)

    detect_holding_changes("2026-06-24", "2026-06-23")
    detect_holding_changes("2026-06-24", "2026-06-23")

    rows = fetch_diagnostics()
    assert len(rows) == 1
    assert rows[0]["etf_code"] == "00980A"
    assert rows[0]["status"] == "included"
