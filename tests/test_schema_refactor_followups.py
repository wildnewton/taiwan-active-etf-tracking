import importlib
import sqlite3

import pytest

import db
import manager_intent
import report


DATES = [
    "2026-07-21",
    "2026-07-22",
    "2026-07-23",
    "2026-07-24",
    "2026-07-25",
]


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _seed_legacy_derived_schema(db_path):
    db.init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TABLE etf_holding_changes")
        conn.execute("DROP TABLE etf_change_diagnostics")
        conn.execute(
            """
            CREATE TABLE etf_holding_changes (
                date TEXT NOT NULL,
                etf_code TEXT NOT NULL,
                issuer TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                weight_delta_1d REAL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (date, etf_code, stock_code)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE etf_change_diagnostics (
                date TEXT NOT NULL,
                prev_date TEXT NOT NULL,
                etf_code TEXT NOT NULL,
                status TEXT NOT NULL,
                current_quality_score REAL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (date, prev_date, etf_code)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE manager_intent_rollups (
                date TEXT NOT NULL,
                window_days INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE etf_manager_signals (
                date TEXT NOT NULL,
                signal_id TEXT PRIMARY KEY,
                signal_type TEXT NOT NULL,
                signal_strength TEXT NOT NULL,
                signal_score REAL NOT NULL,
                stock_code TEXT NOT NULL,
                etf_codes TEXT NOT NULL,
                issuers TEXT NOT NULL,
                etf_count INTEGER NOT NULL,
                issuer_count INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )


def test_one_time_cutover_rebuilds_only_derived_tables_and_writes_backup(tmp_path):
    db_path = tmp_path / "active-etf.sqlite"
    backup_path = tmp_path / "active-etf.pre-schema-refactor.sqlite"
    _seed_legacy_derived_schema(db_path)

    cutover = importlib.import_module("rebuild_derived_schema")
    summary = cutover.rebuild_derived_schema(
        db_path,
        backup_path=backup_path,
        rebuild=False,
    )

    assert summary["backup_path"] == str(backup_path)
    assert backup_path.exists()
    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "manager_intent_rollups" not in tables
        assert "created_at" not in _columns(conn, "etf_holding_changes")
        assert "current_quality_score" not in _columns(
            conn, "etf_change_diagnostics"
        )
        assert {
            "signal_strength",
            "action_label",
            "created_at",
        }.isdisjoint(_columns(conn, "etf_manager_signals"))


def test_issuer_lookup_propagates_database_errors():
    class BrokenConnection:
        def execute(self, *_args, **_kwargs):
            raise sqlite3.OperationalError("database is locked")

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        manager_intent._issuer_by_etf(BrokenConnection())


def test_comparable_context_propagates_database_errors(monkeypatch):
    def broken_dict_rows(*_args, **_kwargs):
        raise sqlite3.OperationalError("malformed database schema")

    monkeypatch.setattr(manager_intent, "_dict_rows", broken_dict_rows)

    with pytest.raises(sqlite3.OperationalError, match="malformed database schema"):
        manager_intent._comparable_context(
            object(),
            ["2026-07-25"],
            {"2026-07-25": {"00980A"}},
            {("2026-07-25", "00980A"): "moneydj_primary"},
        )


def _seed_manager_intent_integration_data():
    db.init_db(":memory:")
    with db._connect() as conn:
        for code, issuer in (("00980A", "Issuer A"), ("00982A", "Issuer B")):
            conn.execute(
                """
                INSERT INTO etf_universe (
                    code, name, issuer, market, listing_date, retired,
                    first_seen_date, created_at, updated_at
                ) VALUES (?, ?, ?, 'TWSE', '2026-01-01', 0,
                          '2026-01-01', '2026-01-01', '2026-01-01')
                """,
                (code, code, issuer),
            )
            for date in DATES:
                conn.execute(
                    """
                    INSERT INTO etf_daily_holdings (
                        date, etf_code, asset_name, asset_type, stock_code,
                        stock_name, shares, weight_pct, source_url, source_type,
                        extraction_method, scraped_at
                    ) VALUES (?, ?, '台積電', 'stock', '2330', '台積電',
                              1000, 5.0, 'https://test', 'moneydj_primary',
                              'test', ?)
                    """,
                    (date, code, date),
                )
                conn.execute(
                    """
                    INSERT INTO etf_daily_non_stock_assets (
                        date, etf_code, asset_name, asset_type, weight_pct,
                        source_url, source_type, extraction_method, scraped_at
                    ) VALUES (?, ?, 'Cash', 'cash', 95.0, 'https://test',
                              'moneydj_primary', 'test', ?)
                    """,
                    (date, code, date),
                )
                conn.execute(
                    """
                    INSERT INTO etf_change_diagnostics (
                        date, prev_date, etf_code, status, reason,
                        current_source_type, previous_source_type,
                        current_stock_count, previous_stock_count,
                        current_total_weight, previous_total_weight,
                        overlap_ratio, size_ratio, created_at
                    ) VALUES (?, '2026-07-20', ?, 'included',
                              'comparable_source_pair', 'moneydj_primary',
                              'moneydj_primary', 1, 1, 100.0, 100.0,
                              1.0, 1.0, ?)
                    """,
                    (date, code, date),
                )
        for code, issuer in (("00980A", "Issuer A"), ("00982A", "Issuer B")):
            conn.execute(
                """
                INSERT INTO etf_holding_changes (
                    date, etf_code, issuer, stock_code, stock_name, prev_date,
                    prev_weight_pct, weight_pct, weight_delta_1d,
                    prev_shares, shares, shares_delta_1d,
                    active_shares_delta_1d, active_shares_delta_pct_1d,
                    prev_rank, rank, is_new_position, is_removed_position,
                    consecutive_add_days, consecutive_reduce_days,
                    consecutive_active_add_days, consecutive_active_reduce_days,
                    position_change_type, active_direction, is_active_add,
                    is_active_reduce, is_passive_weight_change,
                    flow_adjusted_direction, confidence
                ) VALUES (
                    '2026-07-25', ?, ?, '2330', '台積電', '2026-07-24',
                    4.0, 5.0, 1.0, 900, 1000, 100, 100, 11.1,
                    1, 1, 0, 0, 1, 0, 1, 0,
                    'confirmed_active_add', 'add', 1, 0, 0, 'add', 'high'
                )
                """,
                (code, issuer),
            )


def test_real_builder_flows_through_report_filter_sort_and_render():
    _seed_manager_intent_integration_data()

    rows = report._get_manager_intent_rows("2026-07-25")
    rendered = "\n".join(report._render_manager_intent_radar(rows))

    assert rows
    assert rows[0]["stock_code"] == "2330"
    assert rows[0]["primary_intent_state"] == "accumulation"
    assert "Manager Intent Radar" in rendered
    assert "2330 台積電" in rendered
    assert "broad active accumulation" in rendered
