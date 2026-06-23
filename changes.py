from datetime import datetime
from math import ceil
from typing import Optional

import db
from config import TRACKED_ETFS, get_etf_config


_VALID_ETF_COUNT = len(TRACKED_ETFS)
_EPSILON = 1e-9


def get_latest_valid_date(min_success_ratio: float = 0.8) -> Optional[str]:
    """Return latest scrape-run date with enough successful ETF scrapes.

    Falls back to the latest holdings date when scrape-run metadata is absent,
    which keeps unit tests and ad-hoc backfills easy to run.
    """
    min_successes = _min_successes(min_success_ratio)
    with db._connect() as conn:
        rows = conn.execute(
            """
            SELECT date, SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes
            FROM etf_scrape_runs
            GROUP BY date
            ORDER BY date DESC
            """
        ).fetchall()
        for row in rows:
            if row[1] >= min_successes:
                return row[0]

        row = conn.execute(
            """
            SELECT MAX(date)
            FROM etf_daily_holdings
            """
        ).fetchone()
    return row[0] if row and row[0] else None


def get_previous_valid_date(
    current_date: str,
    min_success_ratio: float = 0.8,
) -> Optional[str]:
    """Return previous valid scrape-run date before current_date."""
    min_successes = _min_successes(min_success_ratio)
    with db._connect() as conn:
        rows = conn.execute(
            """
            SELECT date, SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes
            FROM etf_scrape_runs
            WHERE date < ?
            GROUP BY date
            ORDER BY date DESC
            """,
            (current_date,),
        ).fetchall()
        for row in rows:
            if row[1] >= min_successes:
                return row[0]

        row = conn.execute(
            """
            SELECT MAX(date)
            FROM etf_daily_holdings
            WHERE date < ?
            """,
            (current_date,),
        ).fetchone()
    return row[0] if row and row[0] else None


def detect_holding_changes(
    current_date: Optional[str] = None,
    previous_date: Optional[str] = None,
    min_success_ratio: float = 0.8,
) -> dict:
    """Compute and persist ETF holding changes for current_date.

    The comparison is ETF-specific and stock-specific. New and removed
    positions are included via an outer join of today's and previous date's
    holdings.
    """
    current_date = current_date or get_latest_valid_date(min_success_ratio)
    if not current_date:
        return _empty_summary(None, None, "no current holdings date")

    previous_date = previous_date or get_previous_valid_date(
        current_date,
        min_success_ratio,
    )
    if not previous_date:
        return _empty_summary(current_date, None, "no previous holdings date")

    current = _load_ranked_holdings(current_date)
    previous = _load_ranked_holdings(previous_date)
    if not current and not previous:
        return _empty_summary(current_date, previous_date, "no holdings rows")

    trading_dates = _holding_dates_through(current_date)
    weight_cache = {date_value: _load_weight_index(date_value) for date_value in trading_dates}

    changes = []
    keys = sorted(set(current) | set(previous))
    for key in keys:
        etf_code, stock_code = key
        today_row = current.get(key)
        prev_row = previous.get(key)
        changes.append(
            _build_change_row(
                current_date=current_date,
                previous_date=previous_date,
                etf_code=etf_code,
                stock_code=stock_code,
                today=today_row,
                previous=prev_row,
                trading_dates=trading_dates,
                weight_cache=weight_cache,
            )
        )

    _persist_changes(current_date, changes)

    return {
        "ok": True,
        "date": current_date,
        "previous_date": previous_date,
        "rows": len(changes),
        "new_positions": sum(row["is_new_position"] for row in changes),
        "removed_positions": sum(row["is_removed_position"] for row in changes),
    }


def _min_successes(min_success_ratio: float) -> int:
    return ceil(_VALID_ETF_COUNT * min_success_ratio)


def _empty_summary(current_date, previous_date, reason: str) -> dict:
    return {
        "ok": False,
        "date": current_date,
        "previous_date": previous_date,
        "rows": 0,
        "new_positions": 0,
        "removed_positions": 0,
        "reason": reason,
    }


def _load_ranked_holdings(date_value: str) -> dict:
    with db._connect() as conn:
        rows = conn.execute(
            """
            SELECT date, etf_code, stock_code, stock_name, shares, weight_pct, source_type
            FROM etf_daily_holdings
            WHERE date = ?
            ORDER BY etf_code, weight_pct DESC, stock_code
            """,
            (date_value,),
        ).fetchall()

    ranked = {}
    rank_by_etf = {}
    for row in rows:
        etf_code = row[1]
        rank_by_etf[etf_code] = rank_by_etf.get(etf_code, 0) + 1
        ranked[(etf_code, row[2])] = {
            "date": row[0],
            "etf_code": etf_code,
            "stock_code": row[2],
            "stock_name": row[3],
            "shares": row[4],
            "weight_pct": row[5],
            "source_type": row[6],
            "rank": rank_by_etf[etf_code],
        }
    return ranked


def _holding_dates_through(current_date: str) -> list[str]:
    with db._connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT date
            FROM etf_daily_holdings
            WHERE date <= ?
            ORDER BY date
            """,
            (current_date,),
        ).fetchall()
    return [row[0] for row in rows]


def _load_weight_index(date_value: str) -> dict:
    with db._connect() as conn:
        rows = conn.execute(
            """
            SELECT etf_code, stock_code, weight_pct
            FROM etf_daily_holdings
            WHERE date = ?
            """,
            (date_value,),
        ).fetchall()
    return {(row[0], row[1]): row[2] for row in rows}


def _build_change_row(
    *,
    current_date: str,
    previous_date: str,
    etf_code: str,
    stock_code: str,
    today: Optional[dict],
    previous: Optional[dict],
    trading_dates: list[str],
    weight_cache: dict[str, dict],
) -> dict:
    today_weight = today["weight_pct"] if today else None
    previous_weight = previous["weight_pct"] if previous else None
    today_shares = today["shares"] if today else None
    previous_shares = previous["shares"] if previous else None
    current_weight_for_delta = today_weight if today_weight is not None else 0.0
    previous_weight_for_delta = previous_weight if previous_weight is not None else 0.0
    weight_delta = current_weight_for_delta - previous_weight_for_delta
    shares_delta = _nullable_delta(today_shares, previous_shares)

    today_rank = today["rank"] if today else None
    previous_rank = previous["rank"] if previous else None
    rank_delta = None
    if today_rank is not None and previous_rank is not None:
        rank_delta = previous_rank - today_rank

    stock_name = None
    if today and today.get("stock_name"):
        stock_name = today["stock_name"]
    elif previous:
        stock_name = previous.get("stock_name")

    source_type = None
    if today and today.get("source_type"):
        source_type = today["source_type"]
    elif previous:
        source_type = previous.get("source_type")

    return {
        "date": current_date,
        "etf_code": etf_code,
        "issuer": _issuer_for(etf_code),
        "stock_code": stock_code,
        "stock_name": stock_name,
        "prev_date": previous_date,
        "prev_weight_pct": previous_weight,
        "weight_pct": today_weight if today_weight is not None else 0.0,
        "weight_delta_1d": weight_delta,
        "weight_delta_pct_1d": _relative_delta_pct(weight_delta, previous_weight),
        "prev_shares": previous_shares,
        "shares": today_shares,
        "shares_delta_1d": shares_delta,
        "shares_delta_pct_1d": _relative_delta_pct(shares_delta, previous_shares),
        "prev_rank": previous_rank,
        "rank": today_rank,
        "rank_delta_1d": rank_delta,
        "is_new_position": 1 if today and not previous else 0,
        "is_removed_position": 1 if previous and not today else 0,
        "weight_delta_3d": _rolling_weight_delta(
            etf_code,
            stock_code,
            current_date,
            3,
            trading_dates,
            weight_cache,
        ),
        "weight_delta_5d": _rolling_weight_delta(
            etf_code,
            stock_code,
            current_date,
            5,
            trading_dates,
            weight_cache,
        ),
        "weight_delta_10d": _rolling_weight_delta(
            etf_code,
            stock_code,
            current_date,
            10,
            trading_dates,
            weight_cache,
        ),
        "consecutive_add_days": _consecutive_direction_days(
            etf_code,
            stock_code,
            current_date,
            trading_dates,
            weight_cache,
            direction="add",
        ),
        "consecutive_reduce_days": _consecutive_direction_days(
            etf_code,
            stock_code,
            current_date,
            trading_dates,
            weight_cache,
            direction="reduce",
        ),
        "source_type": source_type,
        "created_at": datetime.now().isoformat(),
    }


def _issuer_for(etf_code: str) -> str:
    try:
        return get_etf_config(etf_code)["issuer"]
    except ValueError:
        return ""


def _nullable_delta(current, previous):
    if current is None or previous is None:
        return None
    return current - previous


def _relative_delta_pct(delta, previous):
    if delta is None or previous is None or abs(previous) < _EPSILON:
        return None
    return delta / previous * 100.0


def _rolling_weight_delta(
    etf_code: str,
    stock_code: str,
    current_date: str,
    window_size: int,
    trading_dates: list[str],
    weight_cache: dict[str, dict],
):
    dates = [date_value for date_value in trading_dates if date_value <= current_date]
    if len(dates) < window_size:
        return None

    start_date = dates[-window_size]
    current_weight = weight_cache.get(current_date, {}).get((etf_code, stock_code), 0.0)
    start_weight = weight_cache.get(start_date, {}).get((etf_code, stock_code), 0.0)
    return current_weight - start_weight


def _consecutive_direction_days(
    etf_code: str,
    stock_code: str,
    current_date: str,
    trading_dates: list[str],
    weight_cache: dict[str, dict],
    direction: str,
) -> int:
    dates = [date_value for date_value in trading_dates if date_value <= current_date]
    count = 0
    key = (etf_code, stock_code)

    for index in range(len(dates) - 1, 0, -1):
        current_weight = weight_cache.get(dates[index], {}).get(key, 0.0)
        previous_weight = weight_cache.get(dates[index - 1], {}).get(key, 0.0)
        if direction == "add" and current_weight > previous_weight + _EPSILON:
            count += 1
            continue
        if direction == "reduce" and current_weight < previous_weight - _EPSILON:
            count += 1
            continue
        break

    return count


def _persist_changes(current_date: str, changes: list[dict]) -> None:
    with db._connect() as conn:
        with conn:
            conn.execute(
                "DELETE FROM etf_holding_changes WHERE date = ?",
                (current_date,),
            )
            if not changes:
                return
            conn.executemany(
                """
                INSERT OR REPLACE INTO etf_holding_changes (
                    date, etf_code, issuer, stock_code, stock_name,
                    prev_date, prev_weight_pct, weight_pct, weight_delta_1d,
                    weight_delta_pct_1d, prev_shares, shares, shares_delta_1d,
                    shares_delta_pct_1d, prev_rank, rank, rank_delta_1d,
                    is_new_position, is_removed_position, weight_delta_3d,
                    weight_delta_5d, weight_delta_10d, consecutive_add_days,
                    consecutive_reduce_days, source_type, created_at
                ) VALUES (
                    :date, :etf_code, :issuer, :stock_code, :stock_name,
                    :prev_date, :prev_weight_pct, :weight_pct,
                    :weight_delta_1d, :weight_delta_pct_1d, :prev_shares,
                    :shares, :shares_delta_1d, :shares_delta_pct_1d,
                    :prev_rank, :rank, :rank_delta_1d, :is_new_position,
                    :is_removed_position, :weight_delta_3d, :weight_delta_5d,
                    :weight_delta_10d, :consecutive_add_days,
                    :consecutive_reduce_days, :source_type, :created_at
                )
                """,
                changes,
            )
