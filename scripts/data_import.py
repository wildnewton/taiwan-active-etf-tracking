"""Manual gap-only ETF holdings import.

This module is intentionally separate from the production scraper decision tree.
Each supported ETF owns its source-specific parser.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import db
from data_importers.etf_00400a import parse_00400a_file


def parse_file(etf_code: str, file_path: Path) -> dict:
    """Parse one supported ETF source file into canonical snapshot rows."""
    etf_code = str(etf_code).strip().upper()
    file_path = Path(file_path)

    if etf_code != "00400A":
        return {"ok": False, "reason": f"unsupported_etf:{etf_code}"}

    return parse_00400a_file(file_path)


def import_file(etf_code: str, file_path: Path) -> dict:
    """Import a source file only when its ETF/date snapshot is missing."""
    etf_code = str(etf_code).strip().upper()
    parsed = parse_file(etf_code, file_path)
    if parsed.get("ok") is not True:
        return parsed

    data_date = parsed["data_date"]
    if db.snapshot_exists(data_date, etf_code):
        return {
            "ok": False,
            "reason": "snapshot_already_exists",
            "etf_code": etf_code,
            "data_date": data_date.isoformat(),
        }

    stock_rows = parsed["stock_rows"]
    non_stock_rows = parsed["non_stock_rows"]
    persisted = db.replace_daily_snapshot(stock_rows, non_stock_rows)
    if persisted.get("inserted") is not True:
        return {
            "ok": False,
            "reason": persisted.get("reason", "snapshot_not_inserted"),
            "etf_code": etf_code,
            "data_date": data_date.isoformat(),
        }

    return {
        "ok": True,
        "etf_code": etf_code,
        "data_date": data_date.isoformat(),
        "stock_rows": len(stock_rows),
        "non_stock_rows": len(non_stock_rows),
        "source_type": parsed["source_type"],
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import a missing ETF holdings snapshot from a local source file."
    )
    parser.add_argument("--etf", required=True, help="ETF code, currently only 00400A")
    parser.add_argument("--file", required=True, type=Path, help="Local source file")
    return parser


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    db.init_db(db.DEFAULT_DB_PATH)
    result = import_file(args.etf, args.file)

    if result.get("ok") is True:
        print(
            f"Imported {result['etf_code']} {result['data_date']}: "
            f"{result['stock_rows']} stock rows."
        )
        return 0

    print(f"Import failed: {result.get('reason', 'unknown_error')}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
