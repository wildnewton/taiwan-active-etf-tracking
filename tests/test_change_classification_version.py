import sqlite3

import db
from changes import detect_holding_changes


def _columns(table_name):
    with db._connect() as conn:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _insert_etf_universe_entry():
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_universe
                (code, name, issuer, market, retired,
                 first_seen_date, last_active_date, created_at, updated_at)
            VALUES ('ACTIVE', 'Active ETF', 'ActiveAM', 'TWSE', 0,
                    '2026-06-20', '2026-06-23', datetime('now'), datetime('now'))
            """
        )


def _insert_holding(date_value, stock_code, stock_name, shares, weight_pct):
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_daily_holdings (
                date, etf_code, asset_name, asset_type, stock_code, stock_name,
                shares, weight_pct, source_url, source_type, extraction_method,
                scraped_at
            ) VALUES (?, 'ACTIVE', ?, 'stock', ?, ?, ?, ?, 'https://example.test',
                'moneydj_primary', 'test', '2026-06-23T00:00:00')
            """,
            (
                date_value,
                f"{stock_name}({stock_code}.TW)",
                stock_code,
                stock_name,
                shares,
                weight_pct,
            ),
        )


def test_init_db_creates_classification_version_column():
    db.init_db(":memory:")

    assert "classification_version" in _columns("etf_holding_changes")


def test_init_db_migrates_existing_changes_with_v1_default(tmp_path):
    db_path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE etf_holding_changes (
                date TEXT NOT NULL,
                etf_code TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (date, etf_code, stock_code)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO etf_holding_changes (date, etf_code, stock_code, created_at)
            VALUES ('2026-06-23', 'ACTIVE', '2330', '2026-06-23T00:00:00')
            """
        )

    db.init_db(str(db_path))

    with db._connect() as conn:
        version = conn.execute(
            """
            SELECT classification_version
            FROM etf_holding_changes
            WHERE date = '2026-06-23' AND etf_code = 'ACTIVE' AND stock_code = '2330'
            """
        ).fetchone()[0]

    assert version == "v1"


def test_detect_holding_changes_writes_classification_version_v1():
    db.init_db(":memory:")
    _insert_etf_universe_entry()
    _insert_holding("2026-06-20", "2330", "TSMC", 100, 10.0)
    _insert_holding("2026-06-23", "2330", "TSMC", 110, 12.0)

    summary = detect_holding_changes("2026-06-23", "2026-06-20")

    with db._connect() as conn:
        version = conn.execute(
            """
            SELECT classification_version
            FROM etf_holding_changes
            WHERE date = '2026-06-23' AND etf_code = 'ACTIVE' AND stock_code = '2330'
            """
        ).fetchone()[0]

    assert summary["ok"] is True
    assert version == "v1"
