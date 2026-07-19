"""Source-neutral structural validation for one ETF holdings snapshot.

Total-weight ranges are source diagnostics and never determine snapshot validity.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Iterable


MIN_SNAPSHOT_ROWS = 5
MIN_TAIWAN_STOCK_ROWS = 5


def validate_snapshot_rows(rows: Iterable[Any]) -> tuple[bool, str]:
    """Validate the normalized rows that would form one persisted snapshot."""
    rows = list(rows)
    if not rows:
        return False, "empty_rows"
    if any(_value(row, "weight_pct") is None for row in rows):
        return False, "missing_weight_pct"

    parsed_dates = [_parse_date(_value(row, "date")) for row in rows]
    if any(value is None for value in parsed_dates):
        return False, "missing_or_unparseable_date"
    if len(set(parsed_dates)) != 1:
        return False, "inconsistent_dates"

    etf_codes = {_value(row, "etf_code") for row in rows}
    if None in etf_codes or "" in etf_codes or len(etf_codes) != 1:
        return False, "inconsistent_etf_codes"

    source_types = {_value(row, "source_type") for row in rows}
    if None in source_types or "" in source_types or len(source_types) != 1:
        return False, "inconsistent_source_types"

    if len(rows) < MIN_SNAPSHOT_ROWS:
        return False, "fewer_than_5_rows"

    stock_rows = [row for row in rows if _value(row, "asset_type") == "stock"]
    if len(stock_rows) < MIN_TAIWAN_STOCK_ROWS:
        return False, "fewer_than_5_taiwan_stock_rows"
    for row in stock_rows:
        stock_code = str(_value(row, "stock_code") or "")
        stock_name = str(_value(row, "stock_name") or "").strip()
        if not re.fullmatch(r"\d{4}", stock_code) or not stock_name:
            return False, "invalid_taiwan_stock_row"

    return True, "ok"


def snapshot_metrics(rows: Iterable[Any]) -> dict:
    """Return comparison metrics for one already-normalized snapshot."""
    rows = list(rows)
    stock_rows = [row for row in rows if _value(row, "asset_type") == "stock"]
    return {
        "row_count": len(rows),
        "stock_count": len(stock_rows),
        "total_weight": round(
            sum(float(_value(row, "weight_pct") or 0.0) for row in rows),
            10,
        ),
        "shares_coverage": (
            sum(_value(row, "shares") is not None for row in stock_rows)
            / len(stock_rows)
            if stock_rows
            else 0.0
        ),
    }


def _value(row: Any, key: str):
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


def _parse_date(value) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if value is None:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None
