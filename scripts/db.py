import sqlite3
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from snapshot_validation import snapshot_metrics, validate_snapshot_rows
from source_priority import source_priority

DEFAULT_DB_PATH = Path("data/active_etf_holdings.sqlite")
_DB_PATH = DEFAULT_DB_PATH
_MEMORY_CONN = None


_ETF_UNIVERSE_COLUMNS = (
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
)
_LEGACY_ETF_UNIVERSE_COLUMNS = {
    "last_active_date",
    "pending_retirement_since",
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
    if isinstance(row, dict):
        return {key: _serialize(value) for key, value in row.items()}
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
        conn.execute("CREATE TABLE IF NOT EXISTS etf_daily_holdings (date TEXT NOT NULL, etf_code TEXT NOT NULL, asset_name TEXT NOT NULL, asset_type TEXT NOT NULL, stock_code TEXT NOT NULL, stock_name TEXT, shares REAL, weight_pct REAL NOT NULL, source_url TEXT NOT NULL, source_type TEXT NOT NULL, extraction_method TEXT NOT NULL, scraped_at TEXT NOT NULL, PRIMARY KEY (date, etf_code, stock_code, source_type))")
        conn.execute("CREATE TABLE IF NOT EXISTS etf_daily_non_stock_assets (date TEXT NOT NULL, etf_code TEXT NOT NULL, asset_name TEXT NOT NULL, asset_type TEXT NOT NULL, weight_pct REAL NOT NULL, source_url TEXT NOT NULL, source_type TEXT NOT NULL, extraction_method TEXT NOT NULL, scraped_at TEXT NOT NULL, PRIMARY KEY (date, etf_code, asset_name, source_type))")
        # Scrape attempts are operational logs, not canonical business data.
        # Remove the legacy hybrid state table during normal DB initialization.
        conn.execute("DROP TABLE IF EXISTS etf_scrape_runs")
        conn.execute("CREATE TABLE IF NOT EXISTS etf_holding_changes (date TEXT NOT NULL, etf_code TEXT NOT NULL, issuer TEXT NOT NULL, stock_code TEXT NOT NULL, stock_name TEXT, prev_date TEXT, prev_weight_pct REAL, weight_pct REAL, weight_delta_1d REAL, prev_shares REAL, shares REAL, shares_delta_1d REAL, etf_scale_factor REAL, active_shares_delta_1d REAL, active_shares_delta_pct_1d REAL, prev_rank INTEGER, rank INTEGER, is_new_position INTEGER DEFAULT 0, is_removed_position INTEGER DEFAULT 0, consecutive_add_days INTEGER DEFAULT 0, consecutive_reduce_days INTEGER DEFAULT 0, consecutive_active_add_days INTEGER DEFAULT 0, consecutive_active_reduce_days INTEGER DEFAULT 0, position_change_type TEXT DEFAULT 'unchanged', active_direction TEXT DEFAULT 'none', is_active_add INTEGER DEFAULT 0, is_active_reduce INTEGER DEFAULT 0, is_passive_weight_change INTEGER DEFAULT 0, flow_adjusted_direction TEXT DEFAULT 'none', confidence TEXT DEFAULT 'normal', PRIMARY KEY (date, etf_code, stock_code))")
        _ensure_change_diagnostics_table(conn)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_holdings_date_etf ON etf_daily_holdings(date, etf_code)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_holdings_stock_date ON etf_daily_holdings(stock_code, date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_changes_stock_date ON etf_holding_changes(stock_code, date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_etf_universe_retired ON etf_universe(retired, code)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_change_diagnostics_date ON etf_change_diagnostics(date, prev_date, status)")


def _create_etf_universe_table(conn):
    existing_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(etf_universe)").fetchall()
    }
    if existing_columns & _LEGACY_ETF_UNIVERSE_COLUMNS:
        _rebuild_etf_universe_table(conn)
        return
    _create_compact_etf_universe_table(conn)


def _create_compact_etf_universe_table(conn):
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS etf_universe (
            {_ETF_UNIVERSE_COLUMNS_SQL}
        )
        """
    )


def _rebuild_etf_universe_table(conn):
    columns_sql = ", ".join(_ETF_UNIVERSE_COLUMNS)
    conn.execute("SAVEPOINT rebuild_etf_universe")
    try:
        conn.execute("ALTER TABLE etf_universe RENAME TO etf_universe_legacy")
        _create_compact_etf_universe_table(conn)
        conn.execute(
            f"""
            INSERT INTO etf_universe ({columns_sql})
            SELECT {columns_sql}
            FROM etf_universe_legacy
            """
        )
        conn.execute("DROP TABLE etf_universe_legacy")
    except Exception:
        conn.execute("ROLLBACK TO rebuild_etf_universe")
        conn.execute("RELEASE rebuild_etf_universe")
        raise
    conn.execute("RELEASE rebuild_etf_universe")


def _ensure_change_diagnostics_table(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS etf_change_diagnostics (date TEXT NOT NULL, prev_date TEXT NOT NULL, etf_code TEXT NOT NULL, status TEXT NOT NULL, reason TEXT, current_source_type TEXT, previous_source_type TEXT, current_stock_count INTEGER, previous_stock_count INTEGER, current_total_weight REAL, previous_total_weight REAL, overlap_ratio REAL, size_ratio REAL, created_at TEXT NOT NULL, PRIMARY KEY (date, prev_date, etf_code))")


def insert_holdings(rows):
    rows = [_row_dict(row) for row in rows]
    if not rows:
        return
    with _connect() as conn:
        _insert_holdings(conn, rows)


def insert_non_stock_assets(rows):
    rows = [_row_dict(row) for row in rows]
    if not rows:
        return
    with _connect() as conn:
        _insert_non_stock_assets(conn, rows)


def replace_daily_snapshot(stock_rows, non_stock_rows):
    """Atomically replace one ETF/date snapshot when the incoming source wins."""
    stock_rows = [_row_dict(row) for row in stock_rows]
    non_stock_rows = [_row_dict(row) for row in non_stock_rows]
    rows = stock_rows + non_stock_rows
    if not rows:
        return {"inserted": False, "reason": "empty_snapshot"}

    snapshot_key = _snapshot_key(rows)
    source_type = _snapshot_source_type(rows)
    incoming = _snapshot_entry(source_type, stock_rows, non_stock_rows)

    with _connect() as conn:
        existing_entries = _existing_snapshot_entries(conn, *snapshot_key)
        existing_best = _best_snapshot_entry(existing_entries)
        if existing_best and existing_best["source_type"] != source_type:
            incoming_key = _snapshot_sort_key(incoming)
            existing_key = _snapshot_sort_key(existing_best)
            if incoming_key < existing_key:
                _delete_snapshot_sources_except(conn, *snapshot_key, existing_best["source_type"])
                return {
                    "inserted": False,
                    "reason": "existing_higher_priority_source_preserved",
                    "preserved_source_type": existing_best["source_type"],
                    "incoming_source_type": source_type,
                }

        _delete_snapshot(conn, *snapshot_key)
        _insert_holdings(conn, stock_rows)
        _insert_non_stock_assets(conn, non_stock_rows)
        return {"inserted": True, "source_type": source_type}


def snapshot_exists(date_value, etf_code):
    """Return whether a holdings snapshot exists for one ETF/data date."""
    date_value = _serialize(date_value)
    with _connect() as conn:
        holding = conn.execute(
            "SELECT 1 FROM etf_daily_holdings WHERE date = ? AND etf_code = ? LIMIT 1",
            (date_value, etf_code),
        ).fetchone()
        if holding:
            return True
        non_stock = conn.execute(
            "SELECT 1 FROM etf_daily_non_stock_assets WHERE date = ? AND etf_code = ? LIMIT 1",
            (date_value, etf_code),
        ).fetchone()
    return non_stock is not None


def _snapshot_key(rows):
    keys = {(row["date"], row["etf_code"]) for row in rows}
    if len(keys) != 1:
        raise ValueError("snapshot rows must share one date and etf_code")
    return next(iter(keys))


def _snapshot_source_type(rows):
    source_types = {row["source_type"] for row in rows}
    if len(source_types) != 1:
        raise ValueError("snapshot rows must share one source_type")
    return next(iter(source_types))


def _snapshot_entry(source_type, stock_rows, non_stock_rows):
    stock_count = len(stock_rows)
    shares_count = sum(1 for row in stock_rows if row.get("shares") is not None)
    total_weight = sum((row.get("weight_pct") or 0.0) for row in stock_rows)
    return {
        "source_type": source_type,
        "stock_count": stock_count,
        "non_stock_count": len(non_stock_rows),
        "shares_coverage": shares_count / stock_count if stock_count else 0.0,
        "total_weight": total_weight,
    }


def _existing_snapshot_entries(conn, date_value, etf_code):
    grouped = {}
    for source_type, stock_count, shares_count, total_weight in conn.execute(
        """
        SELECT source_type,
               COUNT(*) AS stock_count,
               SUM(CASE WHEN shares IS NOT NULL THEN 1 ELSE 0 END) AS shares_count,
               SUM(weight_pct) AS total_weight
        FROM etf_daily_holdings
        WHERE date = ? AND etf_code = ?
        GROUP BY source_type
        """,
        (date_value, etf_code),
    ).fetchall():
        grouped[source_type] = {
            "source_type": source_type,
            "stock_count": stock_count or 0,
            "non_stock_count": 0,
            "shares_coverage": (shares_count or 0) / stock_count if stock_count else 0.0,
            "total_weight": total_weight or 0.0,
        }

    for source_type, non_stock_count in conn.execute(
        """
        SELECT source_type, COUNT(*) AS non_stock_count
        FROM etf_daily_non_stock_assets
        WHERE date = ? AND etf_code = ?
        GROUP BY source_type
        """,
        (date_value, etf_code),
    ).fetchall():
        entry = grouped.setdefault(
            source_type,
            {
                "source_type": source_type,
                "stock_count": 0,
                "non_stock_count": 0,
                "shares_coverage": 0.0,
                "total_weight": 0.0,
            },
        )
        entry["non_stock_count"] = non_stock_count or 0
    return list(grouped.values())


def _best_snapshot_entry(entries):
    if not entries:
        return None
    return max(entries, key=_snapshot_sort_key)


def _snapshot_sort_key(entry):
    total_weight = entry.get("total_weight") or 0.0
    weight_ok = 80.0 <= total_weight <= 105.0
    return (
        source_priority(entry.get("source_type")),
        entry.get("stock_count") or 0,
        entry.get("shares_coverage") or 0.0,
        1 if weight_ok else 0,
        entry.get("non_stock_count") or 0,
        entry.get("source_type") or "",
    )


def _delete_snapshot(conn, date_value, etf_code):
    conn.execute("DELETE FROM etf_daily_holdings WHERE date = ? AND etf_code = ?", (date_value, etf_code))
    conn.execute("DELETE FROM etf_daily_non_stock_assets WHERE date = ? AND etf_code = ?", (date_value, etf_code))


def _delete_snapshot_sources_except(conn, date_value, etf_code, source_type):
    conn.execute(
        "DELETE FROM etf_daily_holdings WHERE date = ? AND etf_code = ? AND source_type <> ?",
        (date_value, etf_code, source_type),
    )
    conn.execute(
        "DELETE FROM etf_daily_non_stock_assets WHERE date = ? AND etf_code = ? AND source_type <> ?",
        (date_value, etf_code, source_type),
    )


def _insert_holdings(conn, rows):
    if rows:
        conn.executemany("INSERT OR REPLACE INTO etf_daily_holdings (date, etf_code, asset_name, asset_type, stock_code, stock_name, shares, weight_pct, source_url, source_type, extraction_method, scraped_at) VALUES (:date, :etf_code, :asset_name, :asset_type, :stock_code, :stock_name, :shares, :weight_pct, :source_url, :source_type, :extraction_method, :scraped_at)", rows)


def _insert_non_stock_assets(conn, rows):
    if rows:
        conn.executemany("INSERT OR REPLACE INTO etf_daily_non_stock_assets (date, etf_code, asset_name, asset_type, weight_pct, source_url, source_type, extraction_method, scraped_at) VALUES (:date, :etf_code, :asset_name, :asset_type, :weight_pct, :source_url, :source_type, :extraction_method, :scraped_at) VALUES (:date, :etf_code, :asset_name, :asset_type, :weight_pct, :source_url, :source_type, :extraction_method, :scraped_at)", rows)
