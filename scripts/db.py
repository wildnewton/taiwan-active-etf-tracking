import sqlite3
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from source_priority import source_priority

DEFAULT_DB_PATH = Path("data/active_etf_holdings.sqlite")
_DB_PATH = DEFAULT_DB_PATH
_MEMORY_CONN = None

_CHANGE_COLUMN_MIGRATIONS = {
    "shares_delta_3d": "REAL",
    "shares_delta_5d": "REAL",
    "shares_delta_10d": "REAL",
    "consecutive_active_add_days": "INTEGER DEFAULT 0",
    "consecutive_active_reduce_days": "INTEGER DEFAULT 0",
    "position_change_type": "TEXT DEFAULT 'unchanged'",
    "active_direction": "TEXT DEFAULT 'none'",
    "active_delta_source": "TEXT DEFAULT 'shares'",
    "is_active_add": "INTEGER DEFAULT 0",
    "is_active_reduce": "INTEGER DEFAULT 0",
    "is_passive_weight_change": "INTEGER DEFAULT 0",
    "is_mixed_weight_share_signal": "INTEGER DEFAULT 0",
    "confidence": "TEXT DEFAULT 'normal'",
    "classification_version": "TEXT NOT NULL DEFAULT 'v1'",
    "etf_scale_factor": "REAL",
    "expected_shares": "REAL",
    "active_shares_delta_1d": "REAL",
    "active_shares_delta_pct_1d": "REAL",
    "is_flow_scaled_change": "INTEGER DEFAULT 0",
    "flow_adjusted_direction": "TEXT DEFAULT 'none'",
}

_SCRAPE_RUN_COLUMN_MIGRATIONS = {
    "data_date": "TEXT",
}

_ETF_UNIVERSE_COLUMN_MIGRATIONS = {
    "name": "TEXT",
    "issuer": "TEXT",
    "market": "TEXT",
    "isin": "TEXT",
    "listing_date": "TEXT",
    "retired": "INTEGER NOT NULL DEFAULT 0",
    "first_seen_date": "TEXT",
    "last_active_date": "TEXT",
    "pending_retirement_since": "TEXT",
    "official_url": "TEXT",
    "official_method": "TEXT",
    "official_logic": "TEXT",
    "created_at": "TEXT",
    "updated_at": "TEXT",
}

_ETF_UNIVERSE_COLUMNS_SQL = """
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    issuer TEXT,
    market TEXT,
    isin TEXT,
    listing_date TEXT,
    retired INTEGER NOT NULL DEFAULT 0,
    first_seen_date TEXT,
    last_active_date TEXT,
    pending_retirement_since TEXT,
    official_url TEXT,
    official_method TEXT,
    official_logic TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
"""


def _serialize(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, bool):
        return int(value)
    return value


def _row_dict(row):
    return {key: _serialize(value) for key, value in asdict(row).items()}


def _connect():
    if _DB_PATH == ":memory:":
        return _MEMORY_CONN
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(_DB_PATH)


def init_db(db_path):
    global _DB_PATH, _MEMORY_CONN
    if _MEMORY_CONN is not None and db_path != ":memory:":
        _MEMORY_CONN.close()
        _MEMORY_CONN = None
    if db_path == ":memory:":
        _DB_PATH = db_path
        if _MEMORY_CONN is not None:
            _MEMORY_CONN.close()
        _MEMORY_CONN = sqlite3.connect(db_path)
        conn = _MEMORY_CONN
    else:
        _DB_PATH = Path(db_path)
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(_DB_PATH)

    with conn:
        _create_etf_universe_table(conn)
        _ensure_etf_universe_columns(conn)
        conn.execute("CREATE TABLE IF NOT EXISTS etf_daily_holdings (date TEXT NOT NULL, etf_code TEXT NOT NULL, asset_name TEXT NOT NULL, asset_type TEXT NOT NULL, stock_code TEXT NOT NULL, stock_name TEXT, shares REAL, weight_pct REAL NOT NULL, source_url TEXT NOT NULL, source_type TEXT NOT NULL, extraction_method TEXT NOT NULL, scraped_at TEXT NOT NULL, PRIMARY KEY (date, etf_code, stock_code, source_type))")
        conn.execute("CREATE TABLE IF NOT EXISTS etf_daily_non_stock_assets (date TEXT NOT NULL, etf_code TEXT NOT NULL, asset_name TEXT NOT NULL, asset_type TEXT NOT NULL, weight_pct REAL NOT NULL, source_url TEXT NOT NULL, source_type TEXT NOT NULL, extraction_method TEXT NOT NULL, scraped_at TEXT NOT NULL, PRIMARY KEY (date, etf_code, asset_name, source_type))")
        conn.execute("CREATE TABLE IF NOT EXISTS etf_scrape_runs (date TEXT NOT NULL, data_date TEXT, etf_code TEXT NOT NULL, status TEXT NOT NULL, primary_source TEXT NOT NULL, primary_success INTEGER NOT NULL, moneydj_browser_used INTEGER NOT NULL, official_fallback_used INTEGER NOT NULL, official_success INTEGER NOT NULL, rows_extracted INTEGER NOT NULL, stock_rows_extracted INTEGER NOT NULL, non_stock_rows_extracted INTEGER NOT NULL, total_weight_all_rows REAL NOT NULL, total_weight_stock_rows REAL NOT NULL, source_url TEXT, error TEXT, started_at TEXT NOT NULL, finished_at TEXT, PRIMARY KEY (date, etf_code))")
        _ensure_scrape_run_columns(conn)
        conn.execute("CREATE TABLE IF NOT EXISTS etf_holding_changes (date TEXT NOT NULL, etf_code TEXT NOT NULL, issuer TEXT NOT NULL, stock_code TEXT NOT NULL, stock_name TEXT, prev_date TEXT, prev_weight_pct REAL, weight_pct REAL, weight_delta_1d REAL, weight_delta_pct_1d REAL, prev_shares REAL, shares REAL, shares_delta_1d REAL, shares_delta_pct_1d REAL, etf_scale_factor REAL, expected_shares REAL, active_shares_delta_1d REAL, active_shares_delta_pct_1d REAL, prev_rank INTEGER, rank INTEGER, rank_delta_1d INTEGER, is_new_position INTEGER DEFAULT 0, is_removed_position INTEGER DEFAULT 0, weight_delta_3d REAL, weight_delta_5d REAL, weight_delta_10d REAL, shares_delta_3d REAL, shares_delta_5d REAL, shares_delta_10d REAL, consecutive_add_days INTEGER DEFAULT 0, consecutive_reduce_days INTEGER DEFAULT 0, consecutive_active_add_days INTEGER DEFAULT 0, consecutive_active_reduce_days INTEGER DEFAULT 0, position_change_type TEXT DEFAULT 'unchanged', active_direction TEXT DEFAULT 'none', active_delta_source TEXT DEFAULT 'shares', is_active_add INTEGER DEFAULT 0, is_active_reduce INTEGER DEFAULT 0, is_passive_weight_change INTEGER DEFAULT 0, is_mixed_weight_share_signal INTEGER DEFAULT 0, is_flow_scaled_change INTEGER DEFAULT 0, flow_adjusted_direction TEXT DEFAULT 'none', confidence TEXT DEFAULT 'normal', classification_version TEXT NOT NULL DEFAULT 'v1', source_type TEXT, created_at TEXT NOT NULL, PRIMARY KEY (date, etf_code, stock_code))")
        _ensure_change_columns(conn)
        _ensure_change_diagnostics_table(conn)
        _ensure_manager_intent_rollups_table(conn)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_holdings_date_etf ON etf_daily_holdings(date, etf_code)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_holdings_stock_date ON etf_daily_holdings(stock_code, date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_changes_stock_date ON etf_holding_changes(stock_code, date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_etf_universe_retired ON etf_universe(retired, code)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_change_diagnostics_date ON etf_change_diagnostics(date, prev_date, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_manager_intent_rollups_date ON manager_intent_rollups(date, window_days, entity_level)")


def _create_etf_universe_table(conn):
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS etf_universe (
            {_ETF_UNIVERSE_COLUMNS_SQL}
        )
        """
    )


def _ensure_etf_universe_columns(conn):
    existing = {row[1] for row in conn.execute("PRAGMA table_info(etf_universe)").fetchall()}
    if "last_seen_date" in existing or "retired_since" in existing:
        _rebuild_legacy_etf_universe(conn, existing)
        existing = {row[1] for row in conn.execute("PRAGMA table_info(etf_universe)").fetchall()}
    for column_name, column_type in _ETF_UNIVERSE_COLUMN_MIGRATIONS.items():
        if column_name not in existing:
            conn.execute(f"ALTER TABLE etf_universe ADD COLUMN {column_name} {column_type}")


def _legacy_expr(existing, column_name, fallback="NULL"):
    return column_name if column_name in existing else fallback


def _legacy_last_active_expr(existing):
    last_active = _legacy_expr(existing, "last_active_date")
    last_seen = _legacy_expr(existing, "last_seen_date")
    retired_since = _legacy_expr(existing, "retired_since")
    retired = _legacy_expr(existing, "retired", "0")
    first_seen = _legacy_expr(existing, "first_seen_date")
    return (
        f"COALESCE({last_active}, "
        f"CASE WHEN {retired} = 1 THEN {retired_since} ELSE {last_seen} END, "
        f"{last_seen}, {retired_since}, {first_seen})"
    )


def _rebuild_legacy_etf_universe(conn, existing):
    legacy_table = "etf_universe_legacy_migration"
    conn.execute(f"DROP TABLE IF EXISTS {legacy_table}")
    conn.execute(f"ALTER TABLE etf_universe RENAME TO {legacy_table}")
    _create_etf_universe_table(conn)
    conn.execute(
        f"""
        INSERT INTO etf_universe (
            code, name, issuer, market, isin, listing_date, retired,
            first_seen_date, last_active_date, pending_retirement_since,
            official_url, official_method, official_logic,
            created_at, updated_at
        )
        SELECT
            code,
            COALESCE({_legacy_expr(existing, 'name')}, code),
            {_legacy_expr(existing, 'issuer')},
            {_legacy_expr(existing, 'market')},
            {_legacy_expr(existing, 'isin')},
            {_legacy_expr(existing, 'listing_date')},
            COALESCE({_legacy_expr(existing, 'retired')}, 0),
            {_legacy_expr(existing, 'first_seen_date')},
            {_legacy_last_active_expr(existing)},
            {_legacy_expr(existing, 'pending_retirement_since')},
            {_legacy_expr(existing, 'official_url')},
            {_legacy_expr(existing, 'official_method')},
            {_legacy_expr(existing, 'official_logic')},
            COALESCE({_legacy_expr(existing, 'created_at')}, datetime('now')),
            COALESCE({_legacy_expr(existing, 'updated_at')}, datetime('now'))
        FROM {legacy_table}
        """
    )
    conn.execute(f"DROP TABLE {legacy_table}")


def _ensure_change_columns(conn):
    existing = {row[1] for row in conn.execute("PRAGMA table_info(etf_holding_changes)").fetchall()}
    for column_name, column_type in _CHANGE_COLUMN_MIGRATIONS.items():
        if column_name not in existing:
            conn.execute(f"ALTER TABLE etf_holding_changes ADD COLUMN {column_name} {column_type}")


def _ensure_scrape_run_columns(conn):
    existing = {row[1] for row in conn.execute("PRAGMA table_info(etf_scrape_runs)").fetchall()}
    for column_name, column_type in _SCRAPE_RUN_COLUMN_MIGRATIONS.items():
        if column_name not in existing:
            conn.execute(f"ALTER TABLE etf_scrape_runs ADD COLUMN {column_name} {column_type}")


def _ensure_change_diagnostics_table(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS etf_change_diagnostics (date TEXT NOT NULL, prev_date TEXT NOT NULL, etf_code TEXT NOT NULL, status TEXT NOT NULL, reason TEXT, current_source_type TEXT, previous_source_type TEXT, current_source_family TEXT, previous_source_family TEXT, current_stock_count INTEGER, previous_stock_count INTEGER, current_total_weight REAL, previous_total_weight REAL, current_shares_coverage REAL, previous_shares_coverage REAL, current_quality_score REAL, previous_quality_score REAL, overlap_ratio REAL, size_ratio REAL, created_at TEXT NOT NULL, PRIMARY KEY (date, prev_date, etf_code))")


def _ensure_manager_intent_rollups_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS manager_intent_rollups (
            date TEXT NOT NULL,
            window_days INTEGER NOT NULL,
            entity_level TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            issuer TEXT,
            issuer_key TEXT NOT NULL DEFAULT '',
            eligible_days INTEGER NOT NULL,
            buy_days INTEGER NOT NULL,
            sell_days INTEGER NOT NULL,
            buy_day_pct REAL,
            sell_day_pct REAL,
            cum_active_buy_score REAL NOT NULL,
            cum_active_sell_score REAL NOT NULL,
            net_active_score REAL NOT NULL,
            gross_active_score REAL NOT NULL,
            net_to_gross REAL,
            buy_etf_count INTEGER NOT NULL,
            sell_etf_count INTEGER NOT NULL,
            buy_issuer_count INTEGER NOT NULL,
            sell_issuer_count INTEGER NOT NULL,
            rotation_buy_etf_count INTEGER NOT NULL,
            rotation_sell_etf_count INTEGER NOT NULL,
            cross_fund_offset_ratio REAL,
            intent_direction TEXT NOT NULL,
            primary_intent_state TEXT NOT NULL,
            intent_pattern_tags_json TEXT NOT NULL,
            confidence TEXT NOT NULL,
            metric_version TEXT NOT NULL,
            evidence_json TEXT,
            built_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (date, window_days, entity_level, stock_code, issuer_key)
        )
        """
    )
