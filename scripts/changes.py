from datetime import datetime
from math import ceil
from statistics import median
from typing import Optional

import db
from etf_universe import get_active_etf_count, get_etf_config
from source_priority import source_priority


_EPSILON = 1e-9

_WEEKDAYS = ['週一', '週二', '週三', '週四', '週五', '週六', '週日']


def _weekday_label(date_str: str) -> str:
    return _WEEKDAYS[datetime.strptime(date_str, '%Y-%m-%d').weekday()]
_MIN_SCALE_SAMPLE_SIZE = 3
_MIN_ACTIVE_DELTA_PCT = 1.0


def get_latest_valid_date(min_success_ratio: float = 0.8) -> Optional[str]:
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
        row = conn.execute("SELECT MAX(date) FROM etf_daily_holdings").fetchone()
    return row[0] if row and row[0] else None


def get_previous_valid_date(current_date: str, min_success_ratio: float = 0.8) -> Optional[str]:
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
            "SELECT MAX(date) FROM etf_daily_holdings WHERE date < ?",
            (current_date,),
        ).fetchone()
    return row[0] if row and row[0] else None


def detect_holding_changes(current_date: Optional[str] = None, previous_date: Optional[str] = None, min_success_ratio: float = 0.8) -> dict:
    current_date = current_date or get_latest_valid_date(min_success_ratio)
    if not current_date:
        return _empty_summary(None, None, "no current holdings date")

    previous_date = previous_date or get_previous_valid_date(current_date, min_success_ratio)
    if not previous_date:
        return _empty_summary(current_date, None, "no previous holdings date")

    current_sources = _select_canonical_sources(current_date)
    previous_sources = _select_canonical_sources(previous_date)
    diagnostics = _source_pair_diagnostics(current_date, previous_date, current_sources, previous_sources)
    _persist_change_diagnostics(current_date, previous_date, diagnostics)
    comparable_etfs = {row["etf_code"] for row in diagnostics if row["status"] == "included"}
    skipped_etfs = [row["etf_code"] for row in diagnostics if row["status"] == "skipped"]

    current = _load_ranked_holdings(current_date, current_sources, comparable_etfs)
    previous = _load_ranked_holdings(previous_date, previous_sources, comparable_etfs)
    if not current and not previous:
        _persist_changes(current_date, [])
        if skipped_etfs:
            return _empty_summary(current_date, previous_date, "not comparable ETF/date pairs", skipped_etfs=skipped_etfs)
        return _empty_summary(current_date, previous_date, "no holdings rows")

    scale_factors = _estimate_etf_scale_factors(current, previous)
    trading_dates = _holding_dates_through(current_date)
    source_cache = {date_value: _select_canonical_sources(date_value) for date_value in trading_dates}
    weight_cache = {date_value: _load_weight_index(date_value, source_cache.get(date_value, {})) for date_value in trading_dates}
    shares_cache = {date_value: _load_shares_index(date_value, source_cache.get(date_value, {})) for date_value in trading_dates}

    changes = []
    for key in sorted(set(current) | set(previous)):
        etf_code, stock_code = key
        if etf_code not in comparable_etfs:
            continue
        changes.append(
            _build_change_row(
                current_date=current_date,
                previous_date=previous_date,
                etf_code=etf_code,
                stock_code=stock_code,
                today=current.get(key),
                previous=previous.get(key),
                etf_scale_factor=scale_factors.get(etf_code),
                trading_dates=trading_dates,
                weight_cache=weight_cache,
                shares_cache=shares_cache,
            )
        )

    _persist_changes(current_date, changes)

    if not changes and skipped_etfs:
        return _empty_summary(current_date, previous_date, "not comparable ETF/date pairs", skipped_etfs=skipped_etfs)

    return {
        "ok": True,
        "date": current_date,
        "previous_date": previous_date,
        "previous_date_weekday": _weekday_label(previous_date),
        "rows": len(changes),
        "new_positions": sum(row["is_new_position"] for row in changes),
        "removed_positions": sum(row["is_removed_position"] for row in changes),
        "skipped_etfs": skipped_etfs,
    }


def _min_successes(min_success_ratio: float) -> int:
    return ceil(get_active_etf_count() * min_success_ratio)


def _empty_summary(current_date, previous_date, reason: str, skipped_etfs=None) -> dict:
    return {
        "ok": False,
        "date": current_date,
        "previous_date": previous_date,
        "previous_date_weekday": _weekday_label(previous_date) if previous_date else "未知",
        "rows": 0,
        "new_positions": 0,
        "removed_positions": 0,
        "reason": reason,
        "skipped_etfs": skipped_etfs or [],
    }


def _select_canonical_sources(date_value: str) -> dict:
    with db._connect() as conn:
        rows = conn.execute(
            "SELECT etf_code, source_type, stock_code, shares, weight_pct "
            "FROM etf_daily_holdings WHERE date = ?",
            (date_value,),
        ).fetchall()
        retired_rows = conn.execute(
            "SELECT code FROM etf_universe WHERE retired = 1"
        ).fetchall()
    retired_codes = {row[0] for row in retired_rows}

    grouped = {}
    for etf_code, source_type, stock_code, shares, weight_pct in rows:
        if etf_code in retired_codes:
            continue
        key = (etf_code, source_type)
        entry = grouped.setdefault(
            key,
            {
                "etf_code": etf_code,
                "source_type": source_type,
                "source_family": _source_family(source_type),
                "stock_codes": set(),
                "stock_count": 0,
                "shares_count": 0,
                "total_weight": 0.0,
                "quality_score": 0.0,
            },
        )
        entry["stock_codes"].add(stock_code)
        entry["stock_count"] += 1
        if shares is not None:
            entry["shares_count"] += 1
        entry["total_weight"] += weight_pct or 0.0

    selected = {}
    for entry in grouped.values():
        stock_count = entry["stock_count"]
        entry["shares_coverage"] = entry["shares_count"] / stock_count if stock_count else 0.0
        entry["quality_score"] = _source_quality_score(entry)
        current_best = selected.get(entry["etf_code"])
        if current_best is None or _source_sort_key(entry) > _source_sort_key(current_best):
            selected[entry["etf_code"]] = entry
    return selected


def _source_family(source_type: str) -> str:
    source_type = source_type or ""
    if "moneydj" in source_type:
        return "moneydj"
    if "official" in source_type:
        return "official"
    return source_type or "unknown"


def _source_quality_score(entry: dict) -> float:
    priority = source_priority(entry["source_type"])
    stock_count_bonus = entry["stock_count"] * 2.0
    shares_bonus = entry["shares_coverage"] * 10.0
    total_weight = entry["total_weight"]
    weight_bonus = 5.0 if 80.0 <= total_weight <= 105.0 else 0.0
    return priority + stock_count_bonus + shares_bonus + weight_bonus


def _source_sort_key(entry: dict):
    return (entry["quality_score"], entry["stock_count"], source_priority(entry["source_type"]), entry["source_type"])


def _comparable_etfs(current_sources: dict, previous_sources: dict) -> tuple[set[str], list[str]]:
    diagnostics = _source_pair_diagnostics(None, None, current_sources, previous_sources)
    comparable = {row["etf_code"] for row in diagnostics if row["status"] == "included"}
    skipped = [row["etf_code"] for row in diagnostics if row["status"] == "skipped"]
    return comparable, skipped


def _source_pair_diagnostics(current_date, previous_date, current_sources: dict, previous_sources: dict) -> list[dict]:
    rows = []
    now = datetime.now().isoformat()
    for etf_code in sorted(set(current_sources) | set(previous_sources)):
        current = current_sources.get(etf_code)
        previous = previous_sources.get(etf_code)
        overlap_ratio, size_ratio = _source_pair_ratios(current, previous)
        if not current:
            status, reason = "skipped", "missing_current_source"
        elif not previous:
            status, reason = "skipped", "missing_previous_source"
        elif _is_comparable_source_pair(current, previous):
            status, reason = "included", "comparable_source_pair"
        else:
            status, reason = "skipped", "incompatible_source_pair"
        rows.append(_diagnostic_row(current_date, previous_date, etf_code, current, previous, status, reason, overlap_ratio, size_ratio, now))
    return rows


def _diagnostic_row(current_date, previous_date, etf_code, current, previous, status, reason, overlap_ratio, size_ratio, created_at):
    return {
        "date": current_date,
        "prev_date": previous_date,
        "etf_code": etf_code,
        "status": status,
        "reason": reason,
        "current_source_type": _source_attr(current, "source_type"),
        "previous_source_type": _source_attr(previous, "source_type"),
        "current_source_family": _source_attr(current, "source_family"),
        "previous_source_family": _source_attr(previous, "source_family"),
        "current_stock_count": _source_attr(current, "stock_count"),
        "previous_stock_count": _source_attr(previous, "stock_count"),
        "current_total_weight": _source_attr(current, "total_weight"),
        "previous_total_weight": _source_attr(previous, "total_weight"),
        "current_shares_coverage": _source_attr(current, "shares_coverage"),
        "previous_shares_coverage": _source_attr(previous, "shares_coverage"),
        "current_quality_score": _source_attr(current, "quality_score"),
        "previous_quality_score": _source_attr(previous, "quality_score"),
        "overlap_ratio": overlap_ratio,
        "size_ratio": size_ratio,
        "created_at": created_at,
    }


def _source_attr(source, key):
    return source.get(key) if source else None


def _source_pair_ratios(current: dict | None, previous: dict | None) -> tuple[float | None, float | None]:
    if not current or not previous:
        return None, None
    current_codes = current.get("stock_codes", set())
    previous_codes = previous.get("stock_codes", set())
    if not current_codes or not previous_codes:
        return None, None
    return (
        len(current_codes & previous_codes) / max(len(current_codes), len(previous_codes)),
        len(current_codes) / len(previous_codes),
    )


def _is_comparable_source_pair(current: dict, previous: dict) -> bool:
    overlap, current_vs_previous_size = _source_pair_ratios(current, previous)
    if overlap is None or current_vs_previous_size is None:
        return False
    if overlap >= 0.90 and current_vs_previous_size >= 0.70:
        return True
    return current.get("source_family") == previous.get("source_family") and overlap >= 0.50 and current_vs_previous_size >= 0.50


def _load_ranked_holdings(date_value: str, canonical_sources: dict | None = None, allowed_etfs=None) -> dict:
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

    allowed_etfs = set(allowed_etfs) if allowed_etfs is not None else None
    ranked = {}
    rank_by_etf = {}
    for row in rows:
        etf_code = row[1]
        if allowed_etfs is not None and etf_code not in allowed_etfs:
            continue
        if canonical_sources is not None:
            selected = canonical_sources.get(etf_code)
            if not selected or row[6] != selected["source_type"]:
                continue
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
        rows = conn.execute("SELECT DISTINCT date FROM etf_daily_holdings WHERE date <= ? ORDER BY date", (current_date,)).fetchall()
    return [row[0] for row in rows]


def _load_weight_index(date_value: str, canonical_sources: dict | None = None) -> dict:
    with db._connect() as conn:
        rows = conn.execute("SELECT etf_code, stock_code, weight_pct, source_type FROM etf_daily_holdings WHERE date = ?", (date_value,)).fetchall()
    return {(row[0], row[1]): row[2] for row in rows if canonical_sources is None or (row[0] in canonical_sources and row[3] == canonical_sources[row[0]]["source_type"])}


def _load_shares_index(date_value: str, canonical_sources: dict | None = None) -> dict:
    with db._connect() as conn:
        rows = conn.execute("SELECT etf_code, stock_code, shares, source_type FROM etf_daily_holdings WHERE date = ?", (date_value,)).fetchall()
    return {(row[0], row[1]): row[2] for row in rows if canonical_sources is None or (row[0] in canonical_sources and row[3] == canonical_sources[row[0]]["source_type"])}


def _estimate_etf_scale_factors(current: dict, previous: dict) -> dict:
    ratios_by_etf = {}
    for key, today_row in current.items():
        prev_row = previous.get(key)
        if not prev_row:
            continue
        current_shares = today_row.get("shares")
        previous_shares = prev_row.get("shares")
        if current_shares is None or previous_shares is None or previous_shares <= _EPSILON:
            continue
        ratios_by_etf.setdefault(key[0], []).append(current_shares / previous_shares)
    return {etf_code: median(ratios) for etf_code, ratios in ratios_by_etf.items() if len(ratios) >= _MIN_SCALE_SAMPLE_SIZE}


def _build_change_row(*, current_date: str, previous_date: str, etf_code: str, stock_code: str, today: Optional[dict], previous: Optional[dict], etf_scale_factor, trading_dates: list[str], weight_cache: dict[str, dict], shares_cache: dict[str, dict]) -> dict:
    today_weight = today["weight_pct"] if today else None
    previous_weight = previous["weight_pct"] if previous else None
    today_shares = today["shares"] if today else None
    previous_shares = previous["shares"] if previous else None
    weight_delta = (today_weight if today_weight is not None else 0.0) - (previous_weight if previous_weight is not None else 0.0)
    shares_delta = _nullable_delta(today_shares, previous_shares)
    expected_shares = _expected_shares(previous_shares, etf_scale_factor)
    active_shares_delta = _active_shares_delta(today_shares, expected_shares, shares_delta)
    active_delta_denominator = expected_shares if expected_shares is not None else previous_shares
    active_shares_delta_pct = _relative_delta_pct(active_shares_delta, active_delta_denominator)
    is_new_position = 1 if today and not previous else 0
    is_removed_position = 1 if previous and not today else 0
    classification = _classify_position_change(
        shares_delta=shares_delta,
        active_shares_delta=active_shares_delta,
        active_shares_delta_pct=active_shares_delta_pct,
        weight_delta=weight_delta,
        etf_scale_factor=etf_scale_factor,
        is_new_position=bool(is_new_position),
        is_removed_position=bool(is_removed_position),
    )
    today_rank = today["rank"] if today else None
    previous_rank = previous["rank"] if previous else None
    rank_delta = previous_rank - today_rank if today_rank is not None and previous_rank is not None else None
    stock_name = today.get("stock_name") if today and today.get("stock_name") else previous.get("stock_name") if previous else None
    source_type = today.get("source_type") if today and today.get("source_type") else previous.get("source_type") if previous else None
    if etf_scale_factor is None:
        consecutive_active_add_days = _consecutive_active_direction_days(etf_code, stock_code, current_date, trading_dates, shares_cache, direction="add")
        consecutive_active_reduce_days = _consecutive_active_direction_days(etf_code, stock_code, current_date, trading_dates, shares_cache, direction="reduce")
    else:
        consecutive_active_add_days = _flow_adjusted_consecutive_days(etf_code, stock_code, previous_date, classification, "add")
        consecutive_active_reduce_days = _flow_adjusted_consecutive_days(etf_code, stock_code, previous_date, classification, "reduce")
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
        "etf_scale_factor": etf_scale_factor,
        "expected_shares": expected_shares,
        "active_shares_delta_1d": active_shares_delta,
        "active_shares_delta_pct_1d": active_shares_delta_pct,
        "prev_rank": previous_rank,
        "rank": today_rank,
        "rank_delta_1d": rank_delta,
        "is_new_position": is_new_position,
        "is_removed_position": is_removed_position,
        "weight_delta_3d": _rolling_weight_delta(etf_code, stock_code, current_date, 3, trading_dates, weight_cache),
        "weight_delta_5d": _rolling_weight_delta(etf_code, stock_code, current_date, 5, trading_dates, weight_cache),
        "weight_delta_10d": _rolling_weight_delta(etf_code, stock_code, current_date, 10, trading_dates, weight_cache),
        "shares_delta_3d": _rolling_shares_delta(etf_code, stock_code, current_date, 3, trading_dates, shares_cache),
        "shares_delta_5d": _rolling_shares_delta(etf_code, stock_code, current_date, 5, trading_dates, shares_cache),
        "shares_delta_10d": _rolling_shares_delta(etf_code, stock_code, current_date, 10, trading_dates, shares_cache),
        "consecutive_add_days": _consecutive_direction_days(etf_code, stock_code, current_date, trading_dates, weight_cache, direction="add"),
        "consecutive_reduce_days": _consecutive_direction_days(etf_code, stock_code, current_date, trading_dates, weight_cache, direction="reduce"),
        "consecutive_active_add_days": consecutive_active_add_days,
        "consecutive_active_reduce_days": consecutive_active_reduce_days,
        **classification,
        "source_type": source_type,
        "created_at": datetime.now().isoformat(),
    }


def _expected_shares(previous_shares, etf_scale_factor):
    if previous_shares is None or etf_scale_factor is None:
        return None
    return previous_shares * etf_scale_factor


def _active_shares_delta(today_shares, expected_shares, raw_shares_delta):
    if today_shares is not None and expected_shares is not None:
        delta = today_shares - expected_shares
        return 0.0 if abs(delta) <= _EPSILON else delta
    return raw_shares_delta


def _is_material_active_delta(active_shares_delta, active_shares_delta_pct):
    if active_shares_delta is None or abs(active_shares_delta) <= _EPSILON:
        return False
    if active_shares_delta_pct is None:
        return True
    return abs(active_shares_delta_pct) >= _MIN_ACTIVE_DELTA_PCT


def _classify_position_change(*, shares_delta, active_shares_delta, active_shares_delta_pct, weight_delta, etf_scale_factor, is_new_position: bool, is_removed_position: bool) -> dict:
    if is_new_position:
        return _classification("new_position", "add", 1, 0, 0, 0, 0, "add", "high", etf_scale_factor)
    if is_removed_position:
        return _classification("removed_position", "reduce", 0, 1, 0, 0, 0, "reduce", "high", etf_scale_factor)
    if active_shares_delta is None:
        if weight_delta > _EPSILON:
            return _classification("weight_only_increase", "unknown", 0, 0, 0, 0, 0, "unknown", "low", etf_scale_factor)
        if weight_delta < -_EPSILON:
            return _classification("weight_only_decrease", "unknown", 0, 0, 0, 0, 0, "unknown", "low", etf_scale_factor)
        return _classification("unchanged", "none", 0, 0, 0, 0, 0, "none", "low", etf_scale_factor)
    if etf_scale_factor is not None and abs(active_shares_delta) <= _EPSILON and shares_delta is not None:
        if shares_delta > _EPSILON:
            return _classification("flow_scaled_increase", "none", 0, 0, 0, 0, 1, "none", "medium", etf_scale_factor)
        if shares_delta < -_EPSILON:
            return _classification("flow_scaled_decrease", "none", 0, 0, 0, 0, 1, "none", "medium", etf_scale_factor)
    if not _is_material_active_delta(active_shares_delta, active_shares_delta_pct):
        if active_shares_delta > _EPSILON:
            return _classification("immaterial_active_increase", "none", 0, 0, 0, 0, 0, "none", "low", etf_scale_factor)
        if active_shares_delta < -_EPSILON:
            return _classification("immaterial_active_decrease", "none", 0, 0, 0, 0, 0, "none", "low", etf_scale_factor)
    if active_shares_delta > _EPSILON and weight_delta >= -_EPSILON:
        return _classification("confirmed_active_add", "add", 1, 0, 0, 0, 0, "add", "high", etf_scale_factor)
    if active_shares_delta > _EPSILON and weight_delta < -_EPSILON:
        return _classification("mixed_add_but_weight_down", "add", 1, 0, 0, 1, 0, "add", "medium", etf_scale_factor)
    if active_shares_delta < -_EPSILON and weight_delta <= _EPSILON:
        return _classification("confirmed_active_reduce", "reduce", 0, 1, 0, 0, 0, "reduce", "high", etf_scale_factor)
    if active_shares_delta < -_EPSILON and weight_delta > _EPSILON:
        return _classification("mixed_reduce_but_weight_up", "reduce", 0, 1, 0, 1, 0, "reduce", "medium", etf_scale_factor)
    if weight_delta > _EPSILON:
        return _classification("passive_weight_increase", "none", 0, 0, 1, 0, 0, "none", "low", etf_scale_factor)
    if weight_delta < -_EPSILON:
        return _classification("passive_weight_decrease", "none", 0, 0, 1, 0, 0, "none", "low", etf_scale_factor)
    return _classification("unchanged", "none", 0, 0, 0, 0, 0, "none", "high", etf_scale_factor)


def _classification(position_change_type, active_direction, is_active_add, is_active_reduce, is_passive_weight_change, is_mixed_weight_share_signal, is_flow_scaled_change, flow_adjusted_direction, confidence, etf_scale_factor) -> dict:
    active_delta_source = "flow_adjusted_shares" if etf_scale_factor is not None else "shares"
    return {
        "position_change_type": position_change_type,
        "active_direction": active_direction,
        "active_delta_source": active_delta_source,
        "is_active_add": is_active_add,
        "is_active_reduce": is_active_reduce,
        "is_passive_weight_change": is_passive_weight_change,
        "is_mixed_weight_share_signal": is_mixed_weight_share_signal,
        "is_flow_scaled_change": is_flow_scaled_change,
        "flow_adjusted_direction": flow_adjusted_direction,
        "confidence": confidence,
    }


def _flow_adjusted_consecutive_days(etf_code: str, stock_code: str, previous_date: str, classification: dict, direction: str) -> int:
    flag = "is_active_add" if direction == "add" else "is_active_reduce"
    column = "consecutive_active_add_days" if direction == "add" else "consecutive_active_reduce_days"
    if not classification.get(flag) or classification.get("flow_adjusted_direction") != direction:
        return 0
    with db._connect() as conn:
        row = conn.execute(
            f"""
            SELECT {column}, active_direction, flow_adjusted_direction, {flag}
            FROM etf_holding_changes
            WHERE date = ? AND etf_code = ? AND stock_code = ?
            """,
            (previous_date, etf_code, stock_code),
        ).fetchone()
    if row and row[3] and (row[1] == direction or row[2] == direction):
        return (row[0] or 0) + 1
    return 1


def _issuer_for(etf_code: str) -> str:
    try:
        issuer = get_etf_config(etf_code).get("issuer")
        return issuer or ""
    except KeyError:
        return ""


def _nullable_delta(current, previous):
    if current is None or previous is None:
        return None
    return current - previous


def _relative_delta_pct(delta, previous):
    if delta is None or previous is None or abs(previous) < _EPSILON:
        return None
    return delta / previous * 100.0


def _rolling_weight_delta(etf_code: str, stock_code: str, current_date: str, window_size: int, trading_dates: list[str], weight_cache: dict[str, dict]):
    dates = [date_value for date_value in trading_dates if date_value <= current_date]
    if len(dates) < window_size:
        return None
    start_date = dates[-window_size]
    return weight_cache.get(current_date, {}).get((etf_code, stock_code), 0.0) - weight_cache.get(start_date, {}).get((etf_code, stock_code), 0.0)


def _rolling_shares_delta(etf_code: str, stock_code: str, current_date: str, window_size: int, trading_dates: list[str], shares_cache: dict[str, dict]):
    dates = [date_value for date_value in trading_dates if date_value <= current_date]
    if len(dates) < window_size:
        return None
    start_date = dates[-window_size]
    current_shares = shares_cache.get(current_date, {}).get((etf_code, stock_code), 0.0)
    start_shares = shares_cache.get(start_date, {}).get((etf_code, stock_code), 0.0)
    if current_shares is None or start_shares is None:
        return None
    return current_shares - start_shares


def _consecutive_direction_days(etf_code: str, stock_code: str, current_date: str, trading_dates: list[str], weight_cache: dict[str, dict], direction: str) -> int:
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


def _consecutive_active_direction_days(etf_code: str, stock_code: str, current_date: str, trading_dates: list[str], shares_cache: dict[str, dict], direction: str) -> int:
    dates = [date_value for date_value in trading_dates if date_value <= current_date]
    count = 0
    key = (etf_code, stock_code)
    for index in range(len(dates) - 1, 0, -1):
        current_shares = shares_cache.get(dates[index], {}).get(key)
        previous_shares = shares_cache.get(dates[index - 1], {}).get(key)
        if current_shares is None or previous_shares is None:
            break
        if direction == "add" and current_shares > previous_shares + _EPSILON:
            count += 1
            continue
        if direction == "reduce" and current_shares < previous_shares - _EPSILON:
            count += 1
            continue
        break
    return count


def _ensure_change_diagnostics_table(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS etf_change_diagnostics (date TEXT NOT NULL, prev_date TEXT NOT NULL, etf_code TEXT NOT NULL, status TEXT NOT NULL, reason TEXT, current_source_type TEXT, previous_source_type TEXT, current_source_family TEXT, previous_source_family TEXT, current_stock_count INTEGER, previous_stock_count INTEGER, current_total_weight REAL, previous_total_weight REAL, current_shares_coverage REAL, previous_shares_coverage REAL, current_quality_score REAL, previous_quality_score REAL, overlap_ratio REAL, size_ratio REAL, created_at TEXT NOT NULL, PRIMARY KEY (date, prev_date, etf_code))")


def _persist_change_diagnostics(current_date: str, previous_date: str, diagnostics: list[dict]) -> None:
    with db._connect() as conn:
        with conn:
            _ensure_change_diagnostics_table(conn)
            conn.execute("DELETE FROM etf_change_diagnostics WHERE date = ? AND prev_date = ?", (current_date, previous_date))
            if not diagnostics:
                return
            conn.executemany(
                """
                INSERT OR REPLACE INTO etf_change_diagnostics (
                    date, prev_date, etf_code, status, reason,
                    current_source_type, previous_source_type,
                    current_source_family, previous_source_family,
                    current_stock_count, previous_stock_count,
                    current_total_weight, previous_total_weight,
                    current_shares_coverage, previous_shares_coverage,
                    current_quality_score, previous_quality_score,
                    overlap_ratio, size_ratio, created_at
                ) VALUES (
                    :date, :prev_date, :etf_code, :status, :reason,
                    :current_source_type, :previous_source_type,
                    :current_source_family, :previous_source_family,
                    :current_stock_count, :previous_stock_count,
                    :current_total_weight, :previous_total_weight,
                    :current_shares_coverage, :previous_shares_coverage,
                    :current_quality_score, :previous_quality_score,
                    :overlap_ratio, :size_ratio, :created_at
                )
                """,
                diagnostics,
            )


def _persist_changes(current_date: str, changes: list[dict]) -> None:
    with db._connect() as conn:
        with conn:
            conn.execute("DELETE FROM etf_holding_changes WHERE date = ?", (current_date,))
            if not changes:
                return
            conn.executemany(
                """
                INSERT OR REPLACE INTO etf_holding_changes (
                    date, etf_code, issuer, stock_code, stock_name,
                    prev_date, prev_weight_pct, weight_pct, weight_delta_1d,
                    weight_delta_pct_1d, prev_shares, shares, shares_delta_1d,
                    shares_delta_pct_1d, etf_scale_factor, expected_shares,
                    active_shares_delta_1d, active_shares_delta_pct_1d,
                    prev_rank, rank, rank_delta_1d, is_new_position,
                    is_removed_position, weight_delta_3d, weight_delta_5d,
                    weight_delta_10d, shares_delta_3d, shares_delta_5d,
                    shares_delta_10d, consecutive_add_days,
                    consecutive_reduce_days, consecutive_active_add_days,
                    consecutive_active_reduce_days, position_change_type,
                    active_direction, active_delta_source, is_active_add,
                    is_active_reduce, is_passive_weight_change,
                    is_mixed_weight_share_signal, is_flow_scaled_change,
                    flow_adjusted_direction, confidence, source_type, created_at
                ) VALUES (
                    :date, :etf_code, :issuer, :stock_code, :stock_name,
                    :prev_date, :prev_weight_pct, :weight_pct,
                    :weight_delta_1d, :weight_delta_pct_1d, :prev_shares,
                    :shares, :shares_delta_1d, :shares_delta_pct_1d,
                    :etf_scale_factor, :expected_shares, :active_shares_delta_1d,
                    :active_shares_delta_pct_1d, :prev_rank, :rank,
                    :rank_delta_1d, :is_new_position, :is_removed_position,
                    :weight_delta_3d, :weight_delta_5d, :weight_delta_10d,
                    :shares_delta_3d, :shares_delta_5d, :shares_delta_10d,
                    :consecutive_add_days, :consecutive_reduce_days,
                    :consecutive_active_add_days, :consecutive_active_reduce_days,
                    :position_change_type, :active_direction, :active_delta_source,
                    :is_active_add, :is_active_reduce, :is_passive_weight_change,
                    :is_mixed_weight_share_signal, :is_flow_scaled_change,
                    :flow_adjusted_direction, :confidence, :source_type, :created_at
                )
                """,
                changes,
            )
