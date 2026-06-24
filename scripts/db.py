import sqlite3
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path


DEFAULT_DB_PATH = Path("data/etf_holdings.sqlite3")
_DB_PATH = DEFAULT_DB_PATH
_MEMORY_CONN = None


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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS etf_daily_holdings (
                date TEXT NOT NULL,
                etf_code TEXT NOT NULL,
                asset_name TEXT NOT NULL,
                asset_type TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                stock_name TEXT,
                shares REAL,
                weight_pct REAL NOT NULL,
                source_url TEXT NOT NULL,
                source_type TEXT NOT NULL,
                extraction_method TEXT NOT NULL,
                scraped_at TEXT NOT NULL,
                PRIMARY KEY (date, etf_code, stock_code, source_type)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS etf_daily_non_stock_assets (
                date TEXT NOT NULL,
                etf_code TEXT NOT NULL,
                asset_name TEXT NOT NULL,
                asset_type TEXT NOT NULL,
                weight_pct REAL NOT NULL,
                source_url TEXT NOT NULL,
                source_type TEXT NOT NULL,
                extraction_method TEXT NOT NULL,
                scraped_at TEXT NOT NULL,
                PRIMARY KEY (date, etf_code, asset_name, source_type)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS etf_scrape_runs (
                date TEXT NOT NULL,
                etf_code TEXT NOT NULL,
                status TEXT NOT NULL,
                primary_source TEXT NOT NULL,
                primary_success INTEGER NOT NULL,
                moneydj_browser_used INTEGER NOT NULL,
                official_fallback_used INTEGER NOT NULL,
                official_success INTEGER NOT NULL,
                rows_extracted INTEGER NOT NULL,
                stock_rows_extracted INTEGER NOT NULL,
                non_stock_rows_extracted INTEGER NOT NULL,
                total_weight_all_rows REAL NOT NULL,
                total_weight_stock_rows REAL NOT NULL,
                source_url TEXT,
                error TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                PRIMARY KEY (date, etf_code)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS etf_holding_changes (
                date TEXT NOT NULL,
                etf_code TEXT NOT NULL,
                issuer TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                stock_name TEXT,

                prev_date TEXT,
                prev_weight_pct REAL,
                weight_pct REAL,
                weight_delta_1d REAL,
                weight_delta_pct_1d REAL,

                prev_shares REAL,
                shares REAL,
                shares_delta_1d REAL,
                shares_delta_pct_1d REAL,

                prev_rank INTEGER,
                rank INTEGER,
                rank_delta_1d INTEGER,

                is_new_position INTEGER DEFAULT 0,
                is_removed_position INTEGER DEFAULT 0,

                weight_delta_3d REAL,
                weight_delta_5d REAL,
                weight_delta_10d REAL,

                consecutive_add_days INTEGER DEFAULT 0,
                consecutive_reduce_days INTEGER DEFAULT 0,

                source_type TEXT,
                created_at TEXT NOT NULL,

                PRIMARY KEY (date, etf_code, stock_code)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_holdings_date_etf
            ON etf_daily_holdings(date, etf_code)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_holdings_stock_date
            ON etf_daily_holdings(stock_code, date)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_changes_stock_date
            ON etf_holding_changes(stock_code, date)
            """
        )


def insert_holdings(rows):
    rows = [_row_dict(row) for row in rows]
    if not rows:
        return

    with _connect() as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO etf_daily_holdings (
                date, etf_code, asset_name, asset_type, stock_code, stock_name,
                shares, weight_pct, source_url, source_type,
                extraction_method, scraped_at
            ) VALUES (
                :date, :etf_code, :asset_name, :asset_type, :stock_code,
                :stock_name, :shares, :weight_pct,
                :source_url, :source_type, :extraction_method, :scraped_at
            )
            """,
            rows,
        )


def insert_non_stock_assets(rows):
    rows = [_row_dict(row) for row in rows]
    if not rows:
        return

    with _connect() as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO etf_daily_non_stock_assets (
                date, etf_code, asset_name, asset_type,
                weight_pct, source_url, source_type,
                extraction_method, scraped_at
            ) VALUES (
                :date, :etf_code, :asset_name, :asset_type,
                :weight_pct, :source_url, :source_type,
                :extraction_method, :scraped_at
            )
            """,
            rows,
        )


def insert_scrape_run(run):
    row = _row_dict(run)
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO etf_scrape_runs (
                date, etf_code, status, primary_source, primary_success,
                moneydj_browser_used, official_fallback_used, official_success,
                rows_extracted, stock_rows_extracted, non_stock_rows_extracted,
                total_weight_all_rows, total_weight_stock_rows, source_url,
                error, started_at, finished_at
            ) VALUES (
                :date, :etf_code, :status, :primary_source,
                :primary_success, :moneydj_browser_used,
                :official_fallback_used, :official_success, :rows_extracted,
                :stock_rows_extracted, :non_stock_rows_extracted,
                :total_weight_all_rows, :total_weight_stock_rows, :source_url,
                :error, :started_at, :finished_at
            )
            """,
            row,
        )


def get_last_scrape_date(etf_code):
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT MAX(date)
            FROM etf_scrape_runs
            WHERE etf_code = ? AND status = 'success'
            """,
            (etf_code,),
        ).fetchone()
    return row[0] if row and row[0] else None
