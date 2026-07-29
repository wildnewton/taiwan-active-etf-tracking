import sqlite3
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from signal_assessment import ensure_assessment_criteria_table
from snapshot_validation import snapshot_metrics, validate_snapshot_rows
from source_priority import source_priority

DEFAULT_DB_PATH = Path("data/active_etf_holdings.sqlite")
_DB_PATH = DEFAULT_DB_PATH
_MEMORY_CONN = None


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
        ensure_assessment_criteria_table(conn)
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
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS etf_universe (
            {_ETF_UNIVERSE_COLUMNS_SQL}
        )
        """
    )


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
    valid, reason = validate_snapshot_rows(rows)
    if not valid:
        return {"inserted": False, "reason": f"invalid_snapshot:{reason}"}

    snapshot_key = _snapshot_key(rows)
    source_type = _snapshot_source_type(rows)
    incoming = _snapshot_entry(source_type, rows)

    with _connect() as conn:
        existing_entries = _existing_snapshot_entries(conn, *snapshot_key)
        existing_best = _best_snapshot_entry(existing_entries)
        if existing_best:
            same_source = existing_best["source_type"] == source_type
            if (
                not same_source
                and _snapshot_sort_key(incoming) < _snapshot_sort_key(existing_best)
            ):
                _delete_snapshot_sources_except(
                    conn, *snapshot_key, existing_best["source_type"]
                )
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


def compare_snapshot_to_existing(stock_rows, non_stock_rows):
    """Compare a valid incoming snapshot with the canonical persisted snapshot."""
    stock_rows = [_row_dict(row) for row in stock_rows]
    non_stock_rows = [_row_dict(row) for row in non_stock_rows]
    rows = stock_rows + non_stock_rows
    valid, reason = validate_snapshot_rows(rows)
    incoming = _snapshot_entry(_snapshot_source_type(rows), rows) if valid else None
    if not valid:
        return {
            "existing_snapshot_found": False,
            "incoming_valid": False,
            "reason": reason,
        }

    date_value, etf_code = _snapshot_key(rows)
    with _connect() as conn:
        existing = _best_snapshot_entry(
            _existing_snapshot_entries(conn, date_value, etf_code)
        )
    if not existing:
        return {
            "existing_snapshot_found": False,
            "incoming_valid": True,
            "incoming_source_type": incoming["source_type"],
            "incoming_stock_count": incoming["stock_count"],
            "incoming_total_weight": incoming["total_weight"],
        }

    weight_delta = round(
        abs(incoming["total_weight"] - existing["total_weight"]),
        10,
    )
    equivalent = (
        incoming["stock_count"] == existing["stock_count"]
        and weight_delta < 1.0
    )
    return {
        "existing_snapshot_found": True,
        "incoming_valid": True,
        "incoming_source_type": incoming["source_type"],
        "existing_source_type": existing["source_type"],
        "incoming_stock_count": incoming["stock_count"],
        "existing_stock_count": existing["stock_count"],
        "incoming_total_weight": incoming["total_weight"],
        "existing_total_weight": existing["total_weight"],
        "weight_delta_pct_points": weight_delta,
        "equivalent": equivalent,
    }


def _snapshot_exists(conn, date_value, etf_code):
    return _best_snapshot_entry(
        _existing_snapshot_entries(conn, date_value, etf_code)
    ) is not None


def get_canonical_snapshot_entry(data_date, etf_code):
    """Return the highest-ranked valid snapshot entry for one ETF/date."""
    data_date = _serialize(data_date)
    with _connect() as conn:
        entry = _best_snapshot_entry(
            _existing_snapshot_entries(conn, data_date, etf_code)
        )
        if not entry:
            return None
        stock_codes = {
            row[0]
            for row in conn.execute(
                """
                SELECT stock_code
                FROM etf_daily_holdings
                WHERE date = ? AND etf_code = ? AND source_type = ?
                """,
                (data_date, etf_code, entry["source_type"]),
            ).fetchall()
        }
        return {**entry, "stock_codes": stock_codes}


def get_canonical_snapshot_source(data_date, etf_code):
    """Return the canonical valid source type for one ETF/date."""
    entry = get_canonical_snapshot_entry(data_date, etf_code)
    return entry["source_type"] if entry else None


def snapshot_exists(date_value, etf_code):
    """Return whether one valid snapshot exists for an ETF/data date."""
    date_value = _serialize(date_value)
    with _connect() as conn:
        return _snapshot_exists(conn, date_value, etf_code)


def get_snapshot_etf_codes(data_date):
    """Return ETFs with a valid persisted snapshot on ``data_date``."""
    data_date = _serialize(data_date)
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT etf_code FROM etf_daily_holdings WHERE date = ?
            UNION
            SELECT etf_code FROM etf_daily_non_stock_assets WHERE date = ?
            ORDER BY etf_code
            """,
            (data_date, data_date),
        ).fetchall()
        return [
            row[0]
            for row in rows
            if _snapshot_exists(conn, data_date, row[0])
        ]


def get_latest_snapshot_date(etf_code, before_date=None, eligible_only=False):
    """Return the latest valid snapshot date, optionally historically eligible."""
    if before_date is not None:
        before_date = _serialize(before_date)
        params = [etf_code, before_date, etf_code, before_date]
    else:
        params = [etf_code, etf_code]
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT date
            FROM (
                SELECT date FROM etf_daily_holdings
                WHERE etf_code = ? {"AND date < ?" if before_date is not None else ""}
                UNION
                SELECT date FROM etf_daily_non_stock_assets
                WHERE etf_code = ? {"AND date < ?" if before_date is not None else ""}
            )
            ORDER BY date DESC
            """,
            params,
        ).fetchall()
        for row in rows:
            if eligible_only:
                from etf_universe import get_eligible_etf_codes

                if etf_code not in get_eligible_etf_codes(row[0]):
                    continue
            if _snapshot_exists(conn, row[0], etf_code):
                return row[0]
    return None


def get_target_snapshot_coverage(data_date):
    """Return persisted holdings coverage for one candidate data date."""
    from etf_universe import get_eligible_etf_codes

    data_date = _serialize(data_date)
    expected = set(get_eligible_etf_codes(data_date))
    persisted = set(get_snapshot_etf_codes(data_date))
    actual = persisted & expected
    missing = sorted(expected - actual)
    latest_available = {
        etf_code: get_latest_snapshot_date(
            etf_code,
            before_date=data_date,
            eligible_only=True,
        )
        for etf_code in missing
    }
    return {
        "date": data_date,
        "expected_etf_codes": sorted(expected),
        "actual_etf_codes": sorted(actual),
        "missing_etfs": missing,
        "latest_available_dates": latest_available,
        "expected_count": len(expected),
        "actual_count": len(actual),
    }


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


def _snapshot_entry(source_type, rows):
    valid, validation_reason = validate_snapshot_rows(rows)
    metrics = snapshot_metrics(rows)
    return {
        "source_type": source_type,
        "stock_count": metrics["stock_count"],
        "non_stock_count": metrics["row_count"] - metrics["stock_count"],
        "row_count": metrics["row_count"],
        "shares_coverage": metrics["shares_coverage"],
        "total_weight": metrics["total_weight"],
        "valid": valid,
        "validation_reason": validation_reason,
    }


def _existing_snapshot_rows_by_source(conn, date_value, etf_code):
    grouped = {}
    holding_rows = conn.execute(
        """
        SELECT date, etf_code, asset_name, asset_type, stock_code, stock_name,
               shares, weight_pct, source_url, source_type, extraction_method,
               scraped_at
        FROM etf_daily_holdings
        WHERE date = ? AND etf_code = ?
        """,
        (date_value, etf_code),
    ).fetchall()
    for row in holding_rows:
        item = dict(zip(
            (
                "date", "etf_code", "asset_name", "asset_type", "stock_code",
                "stock_name", "shares", "weight_pct", "source_url",
                "source_type", "extraction_method", "scraped_at",
            ),
            row,
        ))
        grouped.setdefault(item["source_type"], []).append(item)

    non_stock_rows = conn.execute(
        """
        SELECT date, etf_code, asset_name, asset_type, weight_pct, source_url,
               source_type, extraction_method, scraped_at
        FROM etf_daily_non_stock_assets
        WHERE date = ? AND etf_code = ?
        """,
        (date_value, etf_code),
    ).fetchall()
    for row in non_stock_rows:
        item = dict(zip(
            (
                "date", "etf_code", "asset_name", "asset_type", "weight_pct",
                "source_url", "source_type", "extraction_method", "scraped_at",
            ),
            row,
        ))
        item.update({"stock_code": None, "stock_name": None, "shares": None})
        grouped.setdefault(item["source_type"], []).append(item)
    return grouped


def _existing_snapshot_entries(conn, date_value, etf_code):
    return [
        _snapshot_entry(source_type, rows)
        for source_type, rows in _existing_snapshot_rows_by_source(
            conn, date_value, etf_code
        ).items()
    ]


def _best_snapshot_entry(entries):
    valid_entries = [entry for entry in entries if entry.get("valid")]
    if not valid_entries:
        return None
    return max(valid_entries, key=_snapshot_sort_key)


def _snapshot_sort_key(entry):
    return (
        source_priority(entry.get("source_type")),
        entry.get("stock_count") or 0,
        entry.get("shares_coverage") or 0.0,
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
        conn.executemany("INSERT OR REPLACE INTO etf_daily_non_stock_assets (date, etf_code, asset_name, asset_type, weight_pct, source_url, source_type, extraction_method, scraped_at) VALUES (:date, :etf_code, :asset_name, :asset_type, :weight_pct, :source_url, :source_type, :extraction_method, :scraped_at)", rows)
