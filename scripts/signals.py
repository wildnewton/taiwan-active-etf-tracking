import json
import sqlite3
from datetime import datetime

import db
from changes import get_latest_valid_date


def score_to_action_label(score):
    if score >= 8:
        return "Strong Watch"
    if score >= 4:
        return "Watch"
    if score >= 1:
        return "Mild Positive"
    if score <= -8:
        return "Strong Reduce Watch"
    if score <= -4:
        return "Reduce Watch"
    if score <= -1:
        return "Mild Negative"
    return "Neutral"


def _dict_factory(cursor, row):
    return {column[0]: row[index] for index, column in enumerate(cursor.description)}


def _ensure_table():
    conn = db._connect()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS etf_manager_signals (
            date TEXT NOT NULL,
            signal_id TEXT PRIMARY KEY,
            signal_type TEXT NOT NULL,
            signal_strength TEXT NOT NULL,
            signal_score REAL NOT NULL,
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            etf_codes TEXT NOT NULL,
            issuers TEXT NOT NULL,
            etf_count INTEGER NOT NULL,
            issuer_count INTEGER NOT NULL,
            explanation TEXT,
            evidence_json TEXT,
            action_label TEXT,
            confidence TEXT,
            signal_freshness TEXT DEFAULT 'current',
            freshness_reason TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    _ensure_signal_columns(conn)


def _ensure_signal_columns(conn):
    existing = {row[1] for row in conn.execute("PRAGMA table_info(etf_manager_signals)").fetchall()}
    if "signal_freshness" not in existing:
        conn.execute("ALTER TABLE etf_manager_signals ADD COLUMN signal_freshness TEXT DEFAULT 'current'")
    if "freshness_reason" not in existing:
        conn.execute("ALTER TABLE etf_manager_signals ADD COLUMN freshness_reason TEXT")


def _load(date):
    conn = db._connect()
    old = conn.row_factory
    conn.row_factory = _dict_factory
    try:
        return conn.execute(
            "SELECT * FROM etf_holding_changes WHERE date = ? ORDER BY stock_code, etf_code",
            (date,),
        ).fetchall()
    finally:
        conn.row_factory = old


def _change_dates_through(date, limit=3):
    conn = db._connect()
    return [
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT date FROM etf_holding_changes WHERE date <= ? ORDER BY date DESC LIMIT ?",
            (date, limit),
        ).fetchall()
    ]


def _previous_change_date(date):
    conn = db._connect()
    row = conn.execute("SELECT MAX(date) FROM etf_holding_changes WHERE date < ?", (date,)).fetchone()
    return row[0] if row and row[0] else None


def _recent(date, limit=3):
    conn = db._connect()
    old = conn.row_factory
    try:
        dates = _change_dates_through(date, limit)
        if not dates:
            return []
        conn.row_factory = _dict_factory
        marks = ",".join("?" for _ in dates)
        return conn.execute(
            "SELECT * FROM etf_holding_changes WHERE date IN (" + marks + ") ORDER BY date, stock_code, etf_code",
            dates,
        ).fetchall()
    finally:
        conn.row_factory = old


def _gte(value, threshold):
    return value is not None and value >= threshold


def _lte(value, threshold):
    return value is not None and value <= threshold


def _positive(value):
    return value is not None and value > 0


def _negative(value):
    return value is not None and value < 0


def _issuers(rows):
    return {row["issuer"] for row in rows if row.get("issuer")}


def _active_delta(row):
    value = row.get("active_shares_delta_1d")
    return value if value is not None else row.get("shares_delta_1d")


def _three_day_fallback_delta(row):
    if row.get("active_shares_delta_1d") is not None:
        return None
    return row.get("shares_delta_3d")


def _active_add_event(row):
    if row.get("is_new_position") and _gte(row.get("weight_pct"), 2.0):
        return True
    if row.get("is_active_add"):
        return True
    if row.get("active_direction") == "add":
        return True
    return False


def _active_reduce_event(row):
    if row.get("is_removed_position") and _gte(row.get("prev_weight_pct"), 2.0):
        return True
    if row.get("is_active_reduce"):
        return True
    if row.get("active_direction") == "reduce":
        return True
    return False


def _consecutive_active_add_event(row):
    if row.get("consecutive_active_add_days", 0) >= 3:
        return _positive(_active_delta(row)) or _positive(_three_day_fallback_delta(row))
    return (
        not row.get("is_passive_weight_change")
        and row.get("consecutive_active_add_days", 0) == 0
        and row.get("consecutive_add_days", 0) >= 3
        and row.get("active_shares_delta_1d") is None
        and _positive(row.get("shares_delta_1d"))
    )


def _consecutive_active_reduce_event(row):
    if row.get("consecutive_active_reduce_days", 0) >= 3:
        return _negative(_active_delta(row)) or _negative(_three_day_fallback_delta(row))
    return (
        not row.get("is_passive_weight_change")
        and row.get("consecutive_active_reduce_days", 0) == 0
        and row.get("consecutive_reduce_days", 0) >= 3
        and row.get("active_shares_delta_1d") is None
        and _negative(row.get("shares_delta_1d"))
    )


def _is_add(row):
    return _active_add_event(row) or _consecutive_active_add_event(row)


def _is_reduce(row):
    return _active_reduce_event(row) or _consecutive_active_reduce_event(row)


def _evidence(rows):
    return [
        {
            "date": row.get("date"),
            "etf_code": row.get("etf_code"),
            "issuer": row.get("issuer"),
            "stock_code": row.get("stock_code"),
            "stock_name": row.get("stock_name"),
            "weight_pct": row.get("weight_pct"),
            "prev_weight_pct": row.get("prev_weight_pct"),
            "weight_delta_3d": row.get("weight_delta_3d"),
            "shares_delta_1d": row.get("shares_delta_1d"),
            "shares_delta_3d": row.get("shares_delta_3d"),
            "active_shares_delta_1d": row.get("active_shares_delta_1d"),
            "active_shares_delta_pct_1d": row.get("active_shares_delta_pct_1d"),
            "position_change_type": row.get("position_change_type"),
            "active_direction": row.get("active_direction"),
            "flow_adjusted_direction": row.get("flow_adjusted_direction"),
            "consecutive_add_days": row.get("consecutive_add_days"),
            "consecutive_reduce_days": row.get("consecutive_reduce_days"),
            "consecutive_active_add_days": row.get("consecutive_active_add_days"),
            "consecutive_active_reduce_days": row.get("consecutive_active_reduce_days"),
        }
        for row in rows
    ]


def _signal(date, signal_type, strength, score, row, rows, signal_freshness="current", freshness_reason=None):
    etfs = sorted({event["etf_code"] for event in rows})
    issuers = sorted(_issuers(rows))
    signal_id = f"{date}:{signal_type}:{row['stock_code']}:{'-'.join(etfs)}"
    return {
        "date": date,
        "signal_id": signal_id,
        "signal_type": signal_type,
        "signal_strength": strength,
        "signal_score": score,
        "stock_code": row["stock_code"],
        "stock_name": row.get("stock_name"),
        "etf_codes": json.dumps(etfs, ensure_ascii=False),
        "issuers": json.dumps(issuers, ensure_ascii=False),
        "etf_count": len(etfs),
        "issuer_count": len(issuers),
        "explanation": signal_type,
        "evidence_json": json.dumps(_evidence(rows), ensure_ascii=False),
        "action_label": score_to_action_label(score),
        "confidence": row.get("confidence") or "normal",
        "signal_freshness": signal_freshness,
        "freshness_reason": freshness_reason,
        "created_at": datetime.now().isoformat(),
    }


def _single_signals(date, rows):
    signals = []
    for row in rows:
        if row["is_new_position"] and _gte(row["weight_pct"], 2.0):
            strength = "strong" if _gte(row["weight_pct"], 3.0) and row.get("rank") is not None and row["rank"] <= 15 else "medium"
            signals.append(_signal(date, "new_core_position", strength, 4, row, [row], "current", "single ETF new core position"))
        if row["is_removed_position"] and _gte(row["prev_weight_pct"], 2.0):
            strength = "strong" if _gte(row["prev_weight_pct"], 3.0) and row.get("prev_rank") is not None and row["prev_rank"] <= 15 else "medium"
            signals.append(_signal(date, "removed_core_position", strength, -5, row, [row], "current", "single ETF removed core position"))
        if _consecutive_active_add_event(row):
            score = 3 + (1 if (_positive(_active_delta(row)) or _positive(_three_day_fallback_delta(row))) else 0)
            signals.append(_signal(date, "consecutive_add_3d", "medium", score, row, [row], "current", "single ETF consecutive active add"))
        if _consecutive_active_reduce_event(row):
            score = -3 + (-1 if (_negative(_active_delta(row)) or _negative(_three_day_fallback_delta(row))) else 0)
            signals.append(_signal(date, "consecutive_reduce_3d", "medium", score, row, [row], "current", "single ETF consecutive active reduce"))
    return signals


def _group_consensus_events(rows, predicate):
    grouped = {}
    for row in rows:
        if predicate(row):
            grouped.setdefault(row["stock_code"], []).append(row)
    return {stock_code: events for stock_code, events in grouped.items() if len(_issuers(events)) >= 2}


def _consensus_groups_for_date(date, predicate):
    if not date:
        return {}
    return _group_consensus_events(_recent(date, 3), predicate)


def _freshness_label(date, events, same_prior_events, opposite_prior_events):
    today_events = [event for event in events if event.get("date") == date]
    current_issuer_count = len(_issuers(events))
    prior_issuer_count = len(_issuers(same_prior_events)) if same_prior_events else 0
    if not today_events:
        return "stale", "consensus remains in the rolling window, but there is no current-day event"
    if opposite_prior_events:
        return "reversal", "current consensus follows an opposite consensus in the previous window"
    if not same_prior_events:
        return "new", "first reaches consensus in the rolling window"
    if current_issuer_count < prior_issuer_count:
        return "fading", f"issuer count declined from {prior_issuer_count} to {current_issuer_count}"
    return "persistent", "consensus continues with current-day evidence"


def _consensus(date):
    rows = _recent(date, 3)
    previous_date = _previous_change_date(date)
    out = []
    specs = [
        ("consensus_add_3d", _is_add, _is_reduce),
        ("consensus_reduce_3d", _is_reduce, _is_add),
    ]
    for signal_type, predicate, opposite_predicate in specs:
        grouped = _group_consensus_events(rows, predicate)
        previous_same = _consensus_groups_for_date(previous_date, predicate)
        previous_opposite = _consensus_groups_for_date(previous_date, opposite_predicate)
        for stock_code, events in grouped.items():
            issuer_count = len(_issuers(events))
            representative = sorted(events, key=lambda event: (event["date"], event["etf_code"]))[-1]
            strong = issuer_count >= 3
            score = 6 if strong else 4
            if signal_type == "consensus_reduce_3d":
                score = -score
            freshness, reason = _freshness_label(
                date,
                events,
                previous_same.get(stock_code),
                previous_opposite.get(stock_code),
            )
            out.append(_signal(date, signal_type, "strong" if strong else "medium", score, representative, events, freshness, reason))
    return out


def _replace(date, signals):
    conn = db._connect()
    with conn:
        conn.execute("DELETE FROM etf_manager_signals WHERE date = ?", (date,))
        if signals:
            conn.executemany(
                """
                INSERT OR REPLACE INTO etf_manager_signals (
                    date, signal_id, signal_type, signal_strength, signal_score,
                    stock_code, stock_name, etf_codes, issuers, etf_count,
                    issuer_count, explanation, evidence_json, action_label,
                    confidence, signal_freshness, freshness_reason, created_at
                ) VALUES (
                    :date, :signal_id, :signal_type, :signal_strength,
                    :signal_score, :stock_code, :stock_name, :etf_codes,
                    :issuers, :etf_count, :issuer_count, :explanation,
                    :evidence_json, :action_label, :confidence,
                    :signal_freshness, :freshness_reason, :created_at
                )
                """,
                signals,
            )


def generate_manager_signals(signal_date=None):
    _ensure_table()
    date = signal_date or get_latest_valid_date()
    if not date:
        return {"ok": False, "date": None, "signals": 0, "reason": "no signal date"}
    signals = _single_signals(date, _load(date)) + _consensus(date)
    unique = {signal["signal_id"]: signal for signal in signals}
    _replace(date, list(unique.values()))
    return {"ok": True, "date": date, "signals": len(unique)}
