"""Manager-intent rollups for Taiwan active ETF holding changes.

This module builds experimental rolling aggregates from ``etf_holding_changes``.
It intentionally stays below the report layer: PR1 creates the table and metrics
that later PRs can surface in the daily signal report.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable

import db
from etf_universe import get_eligible_etf_codes

METRIC_VERSION = "manager_intent_mvp_v1"
DEFAULT_WINDOWS = (5, 10)
SUPPORTED_WINDOWS = set(DEFAULT_WINDOWS)
MIN_ELIGIBLE_DAYS = 3
NET_TO_GROSS_DIRECTIONAL = 0.25
CROSS_FUND_OFFSET_RATIO_THRESHOLD = 0.5
HIGH_GROSS_THRESHOLDS = {5: 8.0, 10: 12.0}
POSITIVE_THRESHOLDS = {5: 4.0, 10: 6.0}
NEGATIVE_THRESHOLDS = {5: -4.0, 10: -6.0}

BASE_ACTIVE_ADD_SCORE = 2.0
BASE_ACTIVE_REDUCE_SCORE = -2.0
NEW_POSITION_SCORE = 4.0
REMOVED_POSITION_SCORE = -4.0
CONSECUTIVE_ACTIVE_ADD_SCORE = 1.5
CONSECUTIVE_ACTIVE_REDUCE_SCORE = -1.5

_INSERT_SQL = """
INSERT OR REPLACE INTO manager_intent_rollups (
    date, window_days, entity_level, stock_code, stock_name,
    issuer, issuer_key, eligible_days, buy_days, sell_days,
    buy_day_pct, sell_day_pct, cum_active_buy_score,
    cum_active_sell_score, net_active_score, gross_active_score,
    net_to_gross, buy_etf_count, sell_etf_count,
    buy_issuer_count, sell_issuer_count, rotation_buy_etf_count,
    rotation_sell_etf_count, cross_fund_offset_ratio,
    intent_direction, primary_intent_state, intent_pattern_tags_json,
    confidence, metric_version, evidence_json, built_at, created_at
) VALUES (
    :date, :window_days, :entity_level, :stock_code, :stock_name,
    :issuer, :issuer_key, :eligible_days, :buy_days, :sell_days,
    :buy_day_pct, :sell_day_pct, :cum_active_buy_score,
    :cum_active_sell_score, :net_active_score, :gross_active_score,
    :net_to_gross, :buy_etf_count, :sell_etf_count,
    :buy_issuer_count, :sell_issuer_count, :rotation_buy_etf_count,
    :rotation_sell_etf_count, :cross_fund_offset_ratio,
    :intent_direction, :primary_intent_state, :intent_pattern_tags_json,
    :confidence, :metric_version, :evidence_json, :built_at, :created_at
)
"""


def generate_manager_intent_rollups(target_date: str | None = None, windows: Iterable[int] = DEFAULT_WINDOWS) -> dict:
    """Rebuild manager-intent rollups for one date.

    The rebuild uses one ``built_at`` timestamp and one transaction for the
    delete/insert phase so report readers do not observe a half-rebuilt date.
    """
    conn = db._connect()
    target_date = target_date or _latest_change_date(conn)
    if not target_date:
        return {"ok": False, "date": None, "rows": 0, "reason": "no holding changes"}

    windows = tuple(int(window) for window in windows)
    _validate_windows(windows)
    built_at = datetime.now(timezone.utc).isoformat()
    rows = []
    for window_days in windows:
        rows.extend(_build_window_rows(conn, target_date, window_days, built_at))

    with conn:
        db._ensure_manager_intent_rollups_table(conn)
        conn.execute("DELETE FROM manager_intent_rollups WHERE date = ?", (target_date,))
        if rows:
            conn.executemany(_INSERT_SQL, rows)

    return {
        "ok": True,
        "date": target_date,
        "windows": list(windows),
        "rows": len(rows),
        "built_at": built_at,
    }


def _validate_windows(windows: tuple[int, ...]) -> None:
    unsupported = sorted({window for window in windows if window not in SUPPORTED_WINDOWS})
    if unsupported:
        raise ValueError(
            "Unsupported manager intent window(s): "
            f"{unsupported}. Supported windows: {sorted(SUPPORTED_WINDOWS)}"
        )


def _build_window_rows(conn, target_date: str, window_days: int, built_at: str) -> list[dict]:
    window_dates = _window_dates(conn, target_date, window_days)
    if not window_dates:
        return []

    eligible_codes_by_date = {
        date_value: set(get_eligible_etf_codes(date_value))
        for date_value in window_dates
    }
    events = _change_events(conn, window_dates, eligible_codes_by_date)
    candidates, stock_names, issuer_by_etf = _candidate_etf_stocks(
        conn, window_dates, events, eligible_codes_by_date
    )
    comparable_context = _comparable_context(
        conn, window_dates, eligible_codes_by_date
    )
    if not comparable_context:
        return []

    eligible_dates = _eligible_dates_by_entity(candidates, issuer_by_etf, comparable_context, window_dates)
    metrics = _event_metrics(events, stock_names)
    rows = []
    created_at = datetime.now(timezone.utc).isoformat()

    for key in sorted(set(eligible_dates) | set(metrics)):
        entity_level, stock_code, issuer_key = key
        issuer = issuer_key if entity_level == "issuer_stock" else None
        entity_dates = eligible_dates.get(key, set())
        metric = metrics.get(key, _empty_metric(stock_names.get(stock_code)))
        rows.append(
            _rollup_row(
                target_date=target_date,
                window_days=window_days,
                entity_level=entity_level,
                stock_code=stock_code,
                stock_name=metric.get("stock_name") or stock_names.get(stock_code),
                issuer=issuer,
                issuer_key=issuer_key,
                eligible_dates=entity_dates,
                metric=metric,
                built_at=built_at,
                created_at=created_at,
            )
        )

    return rows


def _latest_change_date(conn) -> str | None:
    row = conn.execute("SELECT MAX(date) FROM etf_holding_changes").fetchone()
    return row[0] if row and row[0] else None


def _window_dates(conn, target_date: str, window_days: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT date FROM (
            SELECT DISTINCT date FROM etf_change_diagnostics WHERE date <= ?
            UNION
            SELECT DISTINCT date FROM etf_holding_changes WHERE date <= ?
            UNION
            SELECT DISTINCT date FROM etf_daily_holdings WHERE date <= ?
        )
        ORDER BY date DESC
        LIMIT ?
        """,
        (target_date, target_date, target_date, window_days),
    ).fetchall()
    return sorted(row[0] for row in rows)


def _change_events(
    conn,
    window_dates: list[str],
    eligible_codes_by_date: dict[str, set[str]],
) -> list[dict]:
    if not window_dates:
        return []
    rows = _dict_rows(
        conn,
        f"""
        SELECT h.date, h.etf_code, h.issuer, h.stock_code, h.stock_name,
               h.is_new_position, h.is_removed_position,
               h.is_active_add, h.is_active_reduce,
               h.position_change_type, h.active_direction,
               h.active_shares_delta_1d, h.active_shares_delta_pct_1d,
               h.consecutive_active_add_days, h.consecutive_active_reduce_days
        FROM etf_holding_changes h
        WHERE h.date IN ({_placeholders(window_dates)})
        """,
        window_dates,
    )
    return [
        row
        for row in rows
        if row["etf_code"] in eligible_codes_by_date.get(row["date"], set())
    ]


def _candidate_etf_stocks(
    conn,
    window_dates: list[str],
    events: list[dict],
    eligible_codes_by_date: dict[str, set[str]],
) -> tuple[set[tuple[str, str]], dict[str, str], dict[str, str]]:
    candidates: set[tuple[str, str]] = set()
    stock_names: dict[str, str] = {}
    issuer_by_etf = _issuer_by_etf(conn)

    for row in _holding_rows(conn, window_dates, eligible_codes_by_date):
        etf_code = row["etf_code"]
        stock_code = row["stock_code"]
        candidates.add((etf_code, stock_code))
        stock_names.setdefault(stock_code, row.get("stock_name"))

    for event in events:
        etf_code = event["etf_code"]
        stock_code = event["stock_code"]
        candidates.add((etf_code, stock_code))
        if event.get("stock_name"):
            stock_names.setdefault(stock_code, event.get("stock_name"))
        if event.get("issuer"):
            issuer_by_etf.setdefault(etf_code, event.get("issuer"))

    return candidates, stock_names, issuer_by_etf


def _holding_rows(
    conn,
    window_dates: list[str],
    eligible_codes_by_date: dict[str, set[str]],
) -> list[dict]:
    if not window_dates:
        return []
    rows = _dict_rows(
        conn,
        f"""
        SELECT date, etf_code, stock_code, stock_name
        FROM etf_daily_holdings
        WHERE date IN ({_placeholders(window_dates)})
          AND asset_type = 'stock'
        """,
        window_dates,
    )
    return [
        row
        for row in rows
        if row["etf_code"] in eligible_codes_by_date.get(row["date"], set())
    ]


def _issuer_by_etf(conn) -> dict[str, str]:
    try:
        rows = conn.execute("SELECT code, issuer FROM etf_universe").fetchall()
    except Exception:
        return {}
    return {row[0]: row[1] for row in rows if row[1]}


def _comparable_context(
    conn,
    window_dates: list[str],
    eligible_codes_by_date: dict[str, set[str]],
) -> set[tuple[str, str]]:
    """Return comparable ``(date, etf_code)`` pairs.

    Prefer explicit included change diagnostics. For dates with no diagnostics at
    all, fall back to available holdings rows so legacy data can still produce a
    best-effort rollup.
    """
    if not window_dates:
        return set()
    diagnostics = _dict_rows(
        conn,
        f"""
        SELECT date, etf_code, status
        FROM etf_change_diagnostics
        WHERE date IN ({_placeholders(window_dates)})
        """,
        window_dates,
    )
    context = {
        (row["date"], row["etf_code"])
        for row in diagnostics
        if row.get("status") == "included"
        and row["etf_code"] in eligible_codes_by_date.get(row["date"], set())
    }
    dates_with_diagnostics = {row["date"] for row in diagnostics}
    fallback_dates = [date for date in window_dates if date not in dates_with_diagnostics]
    if fallback_dates:
        holdings = _dict_rows(
            conn,
            f"""
            SELECT DISTINCT date, etf_code
            FROM etf_daily_holdings
            WHERE date IN ({_placeholders(fallback_dates)})
            """,
            fallback_dates,
        )
        context.update(
            (row["date"], row["etf_code"])
            for row in holdings
            if row["etf_code"] in eligible_codes_by_date.get(row["date"], set())
        )
    return context


def _eligible_dates_by_entity(
    candidates: set[tuple[str, str]],
    issuer_by_etf: dict[str, str],
    comparable_context: set[tuple[str, str]],
    window_dates: list[str],
) -> dict[tuple[str, str, str], set[str]]:
    eligible = defaultdict(set)
    for etf_code, stock_code in candidates:
        issuer = issuer_by_etf.get(etf_code)
        if not issuer:
            continue
        for date in window_dates:
            if (date, etf_code) not in comparable_context:
                continue
            eligible[("issuer_stock", stock_code, issuer)].add(date)
            eligible[("stock", stock_code, "")].add(date)
    return eligible


def _event_metrics(events: list[dict], stock_names: dict[str, str]) -> dict[tuple[str, str, str], dict]:
    metrics = {}
    issuer_by_etf = {}
    for event in events:
        if event.get("issuer"):
            issuer_by_etf[event["etf_code"]] = event["issuer"]

    for event in events:
        issuer = event.get("issuer") or issuer_by_etf.get(event.get("etf_code"))
        if not issuer:
            continue
        stock_code = event["stock_code"]
        if event.get("stock_name"):
            stock_names.setdefault(stock_code, event.get("stock_name"))
        score = _event_score(event)
        if score == 0:
            continue
        for key in (("issuer_stock", stock_code, issuer), ("stock", stock_code, "")):
            metric = metrics.setdefault(key, _empty_metric(stock_names.get(stock_code)))
            _apply_event(metric, event, issuer, score)
    return metrics


def _empty_metric(stock_name=None) -> dict:
    return {
        "stock_name": stock_name,
        "daily_scores": defaultdict(float),
        "daily_buy_etfs": defaultdict(set),
        "daily_sell_etfs": defaultdict(set),
        "cum_active_buy_score": 0.0,
        "cum_active_sell_score": 0.0,
        "buy_etfs": set(),
        "sell_etfs": set(),
        "buy_issuers": set(),
        "sell_issuers": set(),
        "evidence": [],
    }


def _event_score(event: dict) -> float:
    position_change_type = event.get("position_change_type") or ""
    active_direction = event.get("active_direction") or ""
    if event.get("is_new_position") or position_change_type == "new_position":
        return NEW_POSITION_SCORE
    if event.get("is_removed_position") or position_change_type == "removed_position":
        return REMOVED_POSITION_SCORE
    if event.get("is_active_add") or active_direction == "add" or "active_add" in position_change_type:
        if (event.get("consecutive_active_add_days") or 0) >= 3:
            return CONSECUTIVE_ACTIVE_ADD_SCORE
        return BASE_ACTIVE_ADD_SCORE
    if event.get("is_active_reduce") or active_direction == "reduce" or "active_reduce" in position_change_type:
        if (event.get("consecutive_active_reduce_days") or 0) >= 3:
            return CONSECUTIVE_ACTIVE_REDUCE_SCORE
        return BASE_ACTIVE_REDUCE_SCORE
    return 0.0


def _apply_event(metric: dict, event: dict, issuer: str, score: float) -> None:
    date = event["date"]
    etf_code = event.get("etf_code")
    metric["daily_scores"][date] += score
    evidence = {
        "date": date,
        "etf_code": etf_code,
        "issuer": issuer,
        "score": score,
        "position_change_type": event.get("position_change_type"),
        "active_direction": event.get("active_direction"),
    }
    metric["evidence"].append(evidence)
    if score > 0:
        metric["cum_active_buy_score"] += score
        metric["buy_etfs"].add(etf_code)
        metric["buy_issuers"].add(issuer)
        metric["daily_buy_etfs"][date].add(etf_code)
    else:
        metric["cum_active_sell_score"] += abs(score)
        metric["sell_etfs"].add(etf_code)
        metric["sell_issuers"].add(issuer)
        metric["daily_sell_etfs"][date].add(etf_code)


def _rollup_row(
    *,
    target_date: str,
    window_days: int,
    entity_level: str,
    stock_code: str,
    stock_name: str | None,
    issuer: str | None,
    issuer_key: str,
    eligible_dates: set[str],
    metric: dict,
    built_at: str,
    created_at: str,
) -> dict:
    daily_scores = metric["daily_scores"]
    eligible_days = len(eligible_dates)
    buy_days = sum(1 for date in eligible_dates if daily_scores.get(date, 0.0) > 0)
    sell_days = sum(1 for date in eligible_dates if daily_scores.get(date, 0.0) < 0)
    buy_score = metric["cum_active_buy_score"]
    sell_score = metric["cum_active_sell_score"]
    net_score = buy_score - sell_score
    gross_score = buy_score + sell_score
    net_to_gross = net_score / gross_score if gross_score else None
    buy_etf_count = len(metric["buy_etfs"])
    sell_etf_count = len(metric["sell_etfs"])
    buy_issuer_count = len(metric["buy_issuers"])
    sell_issuer_count = len(metric["sell_issuers"])
    rotation_buy_etf_count, rotation_sell_etf_count = _rotation_etf_counts(metric, entity_level)
    cross_fund_offset_ratio = _offset_ratio(buy_score, sell_score) if rotation_buy_etf_count and rotation_sell_etf_count else None
    classification = _classify(
        entity_level=entity_level,
        window_days=window_days,
        eligible_days=eligible_days,
        net_score=net_score,
        gross_score=gross_score,
        net_to_gross=net_to_gross,
        buy_etf_count=buy_etf_count,
        sell_etf_count=sell_etf_count,
        buy_issuer_count=buy_issuer_count,
        sell_issuer_count=sell_issuer_count,
        rotation_buy_etf_count=rotation_buy_etf_count,
        rotation_sell_etf_count=rotation_sell_etf_count,
        cross_fund_offset_ratio=cross_fund_offset_ratio,
    )
    return {
        "date": target_date,
        "window_days": window_days,
        "entity_level": entity_level,
        "stock_code": stock_code,
        "stock_name": stock_name,
        "issuer": issuer,
        "issuer_key": issuer_key,
        "eligible_days": eligible_days,
        "buy_days": buy_days,
        "sell_days": sell_days,
        "buy_day_pct": buy_days / eligible_days if eligible_days else None,
        "sell_day_pct": sell_days / eligible_days if eligible_days else None,
        "cum_active_buy_score": buy_score,
        "cum_active_sell_score": sell_score,
        "net_active_score": net_score,
        "gross_active_score": gross_score,
        "net_to_gross": net_to_gross,
        "buy_etf_count": buy_etf_count,
        "sell_etf_count": sell_etf_count,
        "buy_issuer_count": buy_issuer_count,
        "sell_issuer_count": sell_issuer_count,
        "rotation_buy_etf_count": rotation_buy_etf_count,
        "rotation_sell_etf_count": rotation_sell_etf_count,
        "cross_fund_offset_ratio": cross_fund_offset_ratio,
        "intent_direction": classification["intent_direction"],
        "primary_intent_state": classification["primary_intent_state"],
        "intent_pattern_tags_json": json.dumps(classification["tags"], ensure_ascii=False),
        "confidence": classification["confidence"],
        "metric_version": METRIC_VERSION,
        "evidence_json": json.dumps(metric["evidence"][:20], ensure_ascii=False),
        "built_at": built_at,
        "created_at": created_at,
    }


def _rotation_etf_counts(metric: dict, entity_level: str) -> tuple[int, int]:
    if entity_level != "issuer_stock":
        return 0, 0
    rotation_buy_etfs = set()
    rotation_sell_etfs = set()
    for date in set(metric["daily_buy_etfs"]) & set(metric["daily_sell_etfs"]):
        buys = metric["daily_buy_etfs"][date]
        sells = metric["daily_sell_etfs"][date]
        rotation_buy_etfs.update(buy for buy in buys if any(sell != buy for sell in sells))
        rotation_sell_etfs.update(sell for sell in sells if any(buy != sell for buy in buys))
    return len(rotation_buy_etfs), len(rotation_sell_etfs)


def _classify(
    *,
    entity_level: str,
    window_days: int,
    eligible_days: int,
    net_score: float,
    gross_score: float,
    net_to_gross: float | None,
    buy_etf_count: int,
    sell_etf_count: int,
    buy_issuer_count: int,
    sell_issuer_count: int,
    rotation_buy_etf_count: int,
    rotation_sell_etf_count: int,
    cross_fund_offset_ratio: float | None,
) -> dict:
    if eligible_days < MIN_ELIGIBLE_DAYS:
        return _classification("neutral", "insufficient_data", ["insufficient_data"], "low")
    if not gross_score or net_to_gross is None:
        return _classification("neutral", "neutral", [], "low")

    abs_net_to_gross = abs(net_to_gross)
    positive_threshold = _window_threshold(POSITIVE_THRESHOLDS, window_days)
    negative_threshold = _window_threshold(NEGATIVE_THRESHOLDS, window_days)
    high_gross_threshold = _window_threshold(HIGH_GROSS_THRESHOLDS, window_days)

    if (
        entity_level == "stock"
        and buy_issuer_count >= 2
        and sell_issuer_count >= 2
        and gross_score >= high_gross_threshold
        and abs_net_to_gross <= NET_TO_GROSS_DIRECTIONAL
    ):
        return _classification("contested", "contested", ["issuer_disagreement", "high_gross_low_net"], "medium")

    if _is_cross_fund_rotation(rotation_buy_etf_count, rotation_sell_etf_count, cross_fund_offset_ratio):
        if net_to_gross >= NET_TO_GROSS_DIRECTIONAL:
            return _classification(
                "rotation_accumulation",
                "cross_fund_rotation_accumulation",
                ["cross_fund_rotation", "rotation_net_accumulation"],
                "medium",
            )
        if net_to_gross <= -NET_TO_GROSS_DIRECTIONAL:
            return _classification(
                "rotation_distribution",
                "cross_fund_rotation_distribution",
                ["cross_fund_rotation", "rotation_net_distribution"],
                "medium",
            )
        return _classification("rotation", "cross_fund_rotation", ["cross_fund_rotation"], "medium")

    if (
        net_score >= positive_threshold
        and net_to_gross >= NET_TO_GROSS_DIRECTIONAL
        and (buy_issuer_count >= 2 or buy_etf_count >= 3)
    ):
        return _classification("accumulation", "accumulation", ["broad_manager_accumulation"], "medium")

    if (
        net_score <= negative_threshold
        and net_to_gross <= -NET_TO_GROSS_DIRECTIONAL
        and (sell_issuer_count >= 2 or sell_etf_count >= 3)
    ):
        return _classification("distribution", "distribution", ["broad_manager_distribution"], "medium")

    if gross_score >= high_gross_threshold and abs_net_to_gross <= NET_TO_GROSS_DIRECTIONAL:
        return _classification("unclear", "high_activity_unclear", ["high_gross_low_net"], "low")

    return _classification("neutral", "neutral", [], "low")


def _is_cross_fund_rotation(rotation_buy_etf_count: int, rotation_sell_etf_count: int, cross_fund_offset_ratio: float | None) -> bool:
    return (
        rotation_buy_etf_count > 0
        and rotation_sell_etf_count > 0
        and cross_fund_offset_ratio is not None
        and cross_fund_offset_ratio >= CROSS_FUND_OFFSET_RATIO_THRESHOLD
    )


def _classification(intent_direction: str, primary_intent_state: str, tags: list[str], confidence: str) -> dict:
    return {
        "intent_direction": intent_direction,
        "primary_intent_state": primary_intent_state,
        "tags": tags,
        "confidence": confidence,
    }


def _window_threshold(thresholds: dict[int, float], window_days: int) -> float:
    return thresholds[window_days]


def _offset_ratio(buy_score: float, sell_score: float) -> float | None:
    if buy_score <= 0 or sell_score <= 0:
        return None
    return min(buy_score, sell_score) / max(buy_score, sell_score)


def _dict_rows(conn, sql: str, params: Iterable = ()) -> list[dict]:
    cursor = conn.execute(sql, tuple(params))
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _placeholders(values: Iterable) -> str:
    return ", ".join("?" for _ in values)
