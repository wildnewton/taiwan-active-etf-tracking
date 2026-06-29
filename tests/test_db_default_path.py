"""Test that db.py uses the correct default database path."""
from pathlib import Path
import db


def test_default_db_path():
    """The default DB path should point to active_etf_holdings.sqlite.

    The nightly pipeline uses --db data/active_etf_holdings.sqlite, but
    db.py's _connect() defaults to etf_holdings.sqlite3, causing
    report generation and other db._connect() callers to hit an empty DB.
    """
    expected = Path("data/active_etf_holdings.sqlite")
    assert db.DEFAULT_DB_PATH == expected, (
        f"Expected {expected}, got {db.DEFAULT_DB_PATH}"
    )
