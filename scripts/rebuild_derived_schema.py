"""One-time cutover from the deployed derived schema to the compact schema.

This is intentionally not a generic migration framework. Holdings and ETF-universe
rows are preserved; recomputable derived tables are backed up, dropped, recreated,
and optionally rebuilt from holdings.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import db
from backfill_changes import backfill_changes
from changes import get_latest_valid_date
from report import generate_signal_report
from signals import _ensure_table as ensure_signal_table

_DERIVED_TABLES = (
    "etf_manager_signals",
    "manager_intent_rollups",
    "etf_change_diagnostics",
    "etf_holding_changes",
)


def _default_backup_path(db_path: Path) -> Path:
    return db_path.with_name(f"{db_path.name}.pre-schema-refactor.bak")


def _backup_database(db_path: Path, backup_path: Path) -> None:
    if backup_path.exists():
        raise FileExistsError(f"backup already exists: {backup_path}")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(db_path)
    target = sqlite3.connect(backup_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def _restore_backup(db_path: Path, backup_path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)
    source = sqlite3.connect(backup_path)
    target = sqlite3.connect(db_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def _drop_derived_tables() -> None:
    with db._connect() as conn:
        with conn:
            for table in _DERIVED_TABLES:
                conn.execute(f'DROP TABLE IF EXISTS "{table}"')


def rebuild_derived_schema(
    db_path,
    *,
    backup_path=None,
    rebuild=True,
) -> dict:
    """Back up and replace only recomputable derived tables.

    On any failure after the backup is created, the original database is restored
    before the exception is re-raised.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    backup_path = Path(backup_path) if backup_path else _default_backup_path(db_path)
    if db_path.resolve() == backup_path.resolve():
        raise ValueError("backup path must differ from database path")

    _backup_database(db_path, backup_path)
    previous_db_path = db._DB_PATH
    try:
        db.init_db(db_path)
        _drop_derived_tables()
        db.init_db(db_path)
        ensure_signal_table()

        backfill_summary = backfill_changes(all_derived=True) if rebuild else None
        data_date = get_latest_valid_date() if rebuild else None
        report_text = generate_signal_report(data_date) if data_date else ""
        return {
            "ok": True,
            "db_path": str(db_path),
            "backup_path": str(backup_path),
            "rebuild": rebuild,
            "backfill_summary": backfill_summary,
            "smoke_report_date": data_date,
            "smoke_report_chars": len(report_text),
        }
    except Exception:
        _restore_backup(db_path, backup_path)
        raise
    finally:
        db._DB_PATH = previous_db_path


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="One-time rebuild of recomputable derived tables for PR #146."
    )
    parser.add_argument("--db", required=True, dest="db_path")
    parser.add_argument("--backup", dest="backup_path")
    parser.add_argument(
        "--no-rebuild",
        action="store_true",
        help="Recreate compact tables without historical backfill or report smoke.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    summary = rebuild_derived_schema(
        args.db_path,
        backup_path=args.backup_path,
        rebuild=not args.no_rebuild,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
