import sqlite3

import pytest

import db


TARGET_COLUMNS = [
    "code",
    "name",
    "issuer",
    "market",
    "isin",
    "listing_date",
    "retired",
    "first_seen_date",
    "official_url",
    "official_method",
    "official_logic",
    "created_at",
    "updated_at",
]
PRODUCTION_LEGACY_COLUMNS = [
    "last_active_date",
    "pending_retirement_since",
]
ROWS = [
    (
        "00980A",
        "主動野村臺灣優選",
        "Nomura",
        "TWSE",
        "TW00000980A",
        "2025-05-05",
        0,
        "2025-05-01",
        "https://example.test/00980A",
        "stealth_api",
        "fundNo=00980A",
        "2025-05-01T00:00:00",
        "2026-07-29T00:00:00",
    ),
    (
        "00981A",
        "主動統一台股增長",
        "Uni-President",
        "TWSE",
        "TW00000981A",
        "2025-05-27",
        1,
        "2025-05-20",
        "https://example.test/00981A",
        "playwright",
        "fundCode=49YTW",
        "2025-05-20T00:00:00",
        "2026-07-28T00:00:00",
    ),
]


def _create_production_universe(db_path, *, rows=ROWS, name_sql="TEXT NOT NULL"):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"""
            CREATE TABLE etf_universe (
                code TEXT PRIMARY KEY,
                name {name_sql},
                issuer TEXT,
                market TEXT,
                isin TEXT,
                retired INTEGER NOT NULL DEFAULT 0,
                first_seen_date TEXT,
                last_active_date TEXT,
                pending_retirement_since TEXT,
                official_url TEXT,
                official_method TEXT,
                official_logic TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                listing_date TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX idx_etf_universe_retired ON etf_universe(retired, code)"
        )
        placeholders = ", ".join("?" for _ in TARGET_COLUMNS)
        conn.executemany(
            f"""
            INSERT INTO etf_universe ({", ".join(TARGET_COLUMNS)})
            VALUES ({placeholders})
            """,
            rows,
        )


def test_migration_scope_matches_production_legacy_columns():
    assert db._LEGACY_ETF_UNIVERSE_COLUMNS == set(PRODUCTION_LEGACY_COLUMNS)


def test_init_db_rebuilds_current_production_etf_universe(tmp_path):
    db_path = tmp_path / "production-schema.sqlite"
    _create_production_universe(db_path)

    db.init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        table_info = conn.execute("PRAGMA table_info(etf_universe)").fetchall()
        columns = [row[1] for row in table_info]
        primary_key_columns = {row[1]: row[5] for row in table_info if row[5]}
        rows = conn.execute(
            f"SELECT {', '.join(TARGET_COLUMNS)} FROM etf_universe ORDER BY code"
        ).fetchall()
        index = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name = ?",
            ("idx_etf_universe_retired",),
        ).fetchone()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]

    assert columns == TARGET_COLUMNS
    assert primary_key_columns == {"code": 1}
    assert rows == ROWS
    assert index == ("idx_etf_universe_retired",)
    assert integrity == "ok"


def test_etf_universe_rebuild_rolls_back_on_copy_failure(tmp_path):
    db_path = tmp_path / "invalid-production-schema.sqlite"
    invalid_row = (
        "00999A",
        None,
        "TestIssuer",
        "TWSE",
        "TW00000999A",
        "2026-07-01",
        0,
        "2026-07-01",
        "https://example.test/00999A",
        "static",
        "test=true",
        "2026-07-01T00:00:00",
        "2026-07-29T00:00:00",
    )
    _create_production_universe(
        db_path,
        rows=[invalid_row],
        name_sql="TEXT",
    )

    with pytest.raises(sqlite3.IntegrityError):
        db.init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(etf_universe)").fetchall()
        }
        matching_tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'etf_universe%'"
        ).fetchall()
        index = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name = ?",
            ("idx_etf_universe_retired",),
        ).fetchone()
        row = conn.execute(
            "SELECT code, name, official_url FROM etf_universe"
        ).fetchone()

    assert set(PRODUCTION_LEGACY_COLUMNS) <= columns
    assert matching_tables == [("etf_universe",)]
    assert index == ("idx_etf_universe_retired",)
    assert row == ("00999A", None, "https://example.test/00999A")


def test_etf_universe_migration_is_idempotent(tmp_path):
    db_path = tmp_path / "production-schema-idempotent.sqlite"
    _create_production_universe(db_path)
    db.init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TRIGGER etf_universe_no_rebuild_marker
            AFTER UPDATE ON etf_universe
            BEGIN
                SELECT 1;
            END
            """
        )

    db.init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        trigger = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name = ?",
            ("etf_universe_no_rebuild_marker",),
        ).fetchone()
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(etf_universe)").fetchall()
        }
        row_count = conn.execute("SELECT COUNT(*) FROM etf_universe").fetchone()[0]

    assert trigger == ("etf_universe_no_rebuild_marker",)
    assert columns == set(TARGET_COLUMNS)
    assert row_count == len(ROWS)
