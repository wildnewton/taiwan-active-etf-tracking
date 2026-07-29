import sqlite3

import pytest

import db
import manager_intent
import report
import rebuild_derived_schema as cutover


DATES = [
    "2026-07-21",
    "2026-07-22",
    "2026-07-23",
    "2026-07-24",
    "2026-07-25",
]


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _insert_valid_snapshot(conn, date, etf_code="00980A", issuer="Issuer A"):
    conn.execute(
        """
        INSERT OR IGNORE INTO etf_universe (
            code, name, issuer, market, listing_date, retired,
            first_seen_date, created_at, updated_at
        ) VALUES (?, ?, ?, 'TWSE', '2026-01-01', 0,
                  '2026-01-01', '2026-01-01', '2026-01-01')
        """,
        (etf_code, etf_code, issuer),
    )
    for stock_code, stock_name, weight in (
        ("2330", "台積電", 8.0),
        ("2317", "鴻海", 6.0),
        ("2454", "聯發科", 5.0),
        ("2308", "台達電", 4.0),
        ("2881", "富邦金", 3.0),
    ):
        conn.execute(
            """
            INSERT INTO etf_daily_holdings (
                date, etf_code, asset_name, asset_type, stock_code,
                stock_name, shares, weight_pct, source_url, source_type,
                extraction_method, scraped_at
            ) VALUES (?, ?, ?, 'stock', ?, ?, 1000, ?, 'https://test',
                      'moneydj_primary', 'test', ?)
            """,
            (date, etf_code, stock_name, stock_code, stock_name, weight, date),
        )
    conn.execute(
        """
        INSERT INTO etf_daily_non_stock_assets (
            date, etf_code, asset_name, asset_type, weight_pct,
            source_url, source_type, extraction_method, scraped_at
        ) VALUES (?, ?, 'Cash', 'cash', 74.0, 'https://test',
                  'moneydj_primary', 'test', ?)
        """,
        (date, etf_code, date),
    )


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
    with sqlite3.connect(db_path) as conn:
        _insert_valid_snapshot(conn, "2026-07-24")

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
        assert conn.execute(
            "SELECT COUNT(*) FROM etf_daily_holdings"
        ).fetchone()[0] == 5
        assert conn.execute(
            "SELECT issuer FROM etf_universe WHERE code = '00980A'"
        ).fetchone() == ("Issuer A",)


def test_cutover_rebuilds_changes_signals_and_report_smoke(tmp_path):
    db_path = tmp_path / "active-etf.sqlite"
    backup_path = tmp_path / "active-etf.pre-schema-refactor.sqlite"
    _seed_legacy_derived_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        _insert_valid_snapshot(conn, "2026-07-24")
        _insert_valid_snapshot(conn, "2026-07-25")
        conn.execute(
            """
            UPDATE etf_daily_holdings
            SET shares = 1100, weight_pct = 9.0
            WHERE date = '2026-07-25' AND stock_code = '2330'
            """
        )
        conn.execute(
            """
            UPDATE etf_daily_non_stock_assets
            SET weight_pct = 73.0
            WHERE date = '2026-07-25'
            """
        )

    summary = cutover.rebuild_derived_schema(
        db_path,
        backup_path=backup_path,
    )

    assert summary["backfill_summary"]["processed_dates"] == ["2026-07-25"]
    assert summary["backfill_summary"]["change_rows"] > 0
    assert summary["smoke_report_date"] == "2026-07-25"
    assert summary["smoke_report_chars"] > 0
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM etf_holding_changes WHERE date = '2026-07-25'"
        ).fetchone()[0] > 0


def test_cutover_restores_original_database_when_rebuild_fails(tmp_path, monkeypatch):
    db_path = tmp_path / "active-etf.sqlite"
    backup_path = tmp_path / "active-etf.pre-schema-refactor.sqlite"
    _seed_legacy_derived_schema(db_path)

    def fail_backfill(**_kwargs):
        raise RuntimeError("forced backfill failure")

    monkeypatch.setattr(cutover, "backfill_changes", fail_backfill)

    with pytest.raises(RuntimeError, match="forced backfill failure"):
        cutover.rebuild_derived_schema(db_path, backup_path=backup_path)

    assert backup_path.exists()
    with sqlite3.connect(db_path) as conn:
        assert "created_at" in _columns(conn, "etf_holding_changes")
        assert "signal_strength" in _columns(conn, "etf_manager_signals")
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = 'manager_intent_rollups'"
        ).fetchone() == ("manager_intent_rollups",)


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
                for stock_code, stock_name in (
                    ("2330", "台積電"),
                    ("2317", "鴻海"),
                    ("2454", "聯發科"),
                    ("2308", "台達電"),
                    ("2881", "富邦金"),
                ):
                    conn.execute(
                        """
                        INSERT INTO etf_daily_holdings (
                            date, etf_code, asset_name, asset_type, stock_code,
                            stock_name, shares, weight_pct, source_url, source_type,
                            extraction_method, scraped_at
                        ) VALUES (?, ?, ?, 'stock', ?, ?, 1000, 5.0,
                                  'https://test', 'moneydj_primary', 'test', ?)
                        """,
                        (date, code, stock_name, stock_code, stock_name, date),
                    )
                conn.execute(
                    """
                    INSERT INTO etf_daily_non_stock_assets (
                        date, etf_code, asset_name, asset_type, weight_pct,
                        source_url, source_type, extraction_method, scraped_at
                    ) VALUES (?, ?, 'Cash', 'cash', 75.0, 'https://test',
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
