"""Trading-day helpers backed by the TW stock-market database.

The stock-market-data-update project maintains the Taiwan market database at
`data/stocks.db`.  This module treats distinct `StockData.date` values in that
DB as the source of truth for TW trading dates.
"""

import os
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Optional


DEFAULT_TW_STOCK_DB_PATH = Path(__file__).resolve().parents[2] / "stock-market-data-update" / "data" / "stocks.db"
TW_STOCK_DB_PATH_ENV = "TW_STOCK_DB_PATH"


def tw_stock_db_path() -> Path:
    configured = os.environ.get(TW_STOCK_DB_PATH_ENV)
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_TW_STOCK_DB_PATH


def latest_tw_trading_day_on_or_before(run_date, db_path: str | Path | None = None) -> Optional[date]:
    """Return the latest TW trading day on or before run_date.

    Returns None when the TW stock DB is unavailable or does not contain a usable
    StockData calendar.  Callers should treat None as "calendar unknown" rather
    than as a non-trading day.
    """
    run_date = _coerce_date(run_date)
    path = Path(db_path).expanduser() if db_path is not None else tw_stock_db_path()
    if not path.exists():
        return None

    try:
        with sqlite3.connect(path) as conn:
            row = conn.execute(
                "SELECT MAX(date) FROM StockData WHERE date <= ?",
                (run_date.isoformat(),),
            ).fetchone()
    except sqlite3.Error:
        return None

    if not row or not row[0]:
        return None
    return _coerce_date(row[0])


def is_tw_trading_day(run_date, db_path: str | Path | None = None) -> Optional[bool]:
    """Return whether run_date is a TW trading day, or None if calendar is unknown."""
    run_date = _coerce_date(run_date)
    latest = latest_tw_trading_day_on_or_before(run_date, db_path=db_path)
    if latest is None:
        return None
    return latest == run_date


def _coerce_date(value) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return datetime.strptime(value, "%Y-%m-%d").date()
    if all(hasattr(value, attr) for attr in ("year", "month", "day", "isoformat")):
        return date(value.year, value.month, value.day)
    raise TypeError("value must be a date or ISO date string")
