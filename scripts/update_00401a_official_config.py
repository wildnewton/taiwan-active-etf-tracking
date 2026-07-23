#!/usr/bin/env python3
"""One-time deployment helper for JPMorgan 00401A official scraper config."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path


ETF_CODE = "00401A"
ETF_NAME = "主動摩根台灣鑫收"
EXPECTED_CONFIG = {
    "issuer": "JPMorgan",
    "official_url": "https://am.jpmorgan.com/FundsMarketingHandler/excel",
    "official_method": "api",
    "official_logic": (
        "type=holding_pcf;cusip=TW00000401A1;country=tw;"
        "role=twetf;locale=zh-TW"
    ),
}
_SELECT_SQL = "SELECT * FROM etf_universe WHERE code = ?"


def _fetch_row(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(_SELECT_SQL, (ETF_CODE,)).fetchone()
    return dict(row) if row else None


def _snapshot(row: dict | None) -> dict | None:
    if row is None:
        return None
    keys = (
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
    return {key: row.get(key) for key in keys}


def _has_expected_config(row: dict | None) -> bool:
    return row is not None and all(
        row.get(key) == value for key, value in EXPECTED_CONFIG.items()
    )


def update_00401a_config(
    db_path: str | Path,
    *,
    dry_run: bool = False,
) -> dict:
    """Ensure the deployed DB routes 00401A to the JPMorgan XLSX endpoint."""
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"Database does not exist: {path}")

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        before = _fetch_row(conn)
        inserted = before is None
        changed = inserted or not _has_expected_config(before)

        if changed:
            now = datetime.now(timezone.utc).isoformat()
            if inserted:
                conn.execute(
                    """
                    INSERT INTO etf_universe (
                        code, name, issuer, market, isin, listing_date,
                        retired, first_seen_date, official_url,
                        official_method, official_logic, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, NULL, NULL, 0, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ETF_CODE,
                        ETF_NAME,
                        EXPECTED_CONFIG["issuer"],
                        "TWSE",
                        date.today().isoformat(),
                        EXPECTED_CONFIG["official_url"],
                        EXPECTED_CONFIG["official_method"],
                        EXPECTED_CONFIG["official_logic"],
                        now,
                        now,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE etf_universe
                    SET issuer = ?,
                        official_url = ?,
                        official_method = ?,
                        official_logic = ?,
                        updated_at = ?
                    WHERE code = ?
                    """,
                    (
                        EXPECTED_CONFIG["issuer"],
                        EXPECTED_CONFIG["official_url"],
                        EXPECTED_CONFIG["official_method"],
                        EXPECTED_CONFIG["official_logic"],
                        now,
                        ETF_CODE,
                    ),
                )

        after = _fetch_row(conn)
        if not _has_expected_config(after):
            raise RuntimeError("00401A config verification failed")

        result = {
            "db_path": str(path),
            "before": _snapshot(before),
            "after": _snapshot(after),
            "changed": changed,
            "inserted": inserted,
            "dry_run": dry_run,
        }
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Upsert the canonical JPMorgan 00401A scraper config.",
    )
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    result = update_00401a_config(args.db, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
