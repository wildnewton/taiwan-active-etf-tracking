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
            created_at TEXT NOT NULL
        )
        """
    )


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


def _recent(date, limit=3):
    conn = db._connect()
    old = conn.row_factory
    try:
        conn.row_factory = None
        dates = [r[0] for r in conn.execute(
            "SELECT DISTINCT date FROM etf_holding_changes WHERE date <= ? ORDER BY date DESC LIMIT ?",
            (date, limit),
        ).fetchall()]
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


def _positive(value):
    return value is not None and value > 0


def _negative(value):
    return value is not None and value < 0


def _issuers(rows):
    return {r["issuer"] for r in rows if r.get("issuer")}


def _active_delta(r):
    value = r.get("active_shares_delta_1d")
    return value if value is not None else r.get("shares_delta_1d")


def _active_add_event(r):
    if r.get("is_new_position") and _gte(r.get("weight_pct"), 2.0):
        return True
    if r.get("is_active_add"):
        return True
    if r.get("active_direction") == "add":
        return True
    return False


def _active_reduce_event(r):
    if r.get("is_removed_position") and _gte(r.get("prev_weight_pct"), 2.0):
        return True
    if r.get("is_active_reduce"):
        return True
    if r.get("active_direction") == "reduce":
        return True
    return False


def _consecutive_active_add_event(r):
    if r.get("consecutive_active_add_days", 0) >= 3 and _positive(_active_delta(r)):
        return True
    return (
        not r.get("is_passive_weight_change")
        and r.get("consecutive_active_add_days", 0) == 0
        and r.get("consecutive_add_days", 0) >= 3
        and r.get("active_shares_delta_1d") is None
        and _positive(r.get("shares_delta_1d"))
    )


def _consecutive_active_reduce_event(r):
    if r.get("consecutive_active_reduce_days", 0) >= 3 and _negative(_active_delta(r)):
        return True
    return (
        not r.get("is_passive_weight_change")
        and r.get("consecutive_active_reduce_days", 0) == 0
        and r.get("consecutive_reduce_days", 0) >= 3
        and r.get("active_shares_delta_1d") is None
        and _negative(r.get("shares_delta_1d"))
    )


def _is_add(r):
    return _active_add_event(r) or _consecutive_active_add_event(r)


def _is_reduce(r):
    return _active_reduce_event(r) or _consecutive_active_reduce_event(r)


def _evidence(rows):
    return [
        {
            "date": r.get("date"),
            "etf_code": r.get("etf_code"),
            "issuer": r.get("issuer"),
            "stock_code": r.get("stock_code"),
            "stock_name": r.get("stock_name"),
            "weight_pct": r.get("weight_pct"),
            "prev_weight_pct": r.get("prev_weight_pct"),
            "weight_delta_3d": r.get("weight_delta_3d"),
            "shares_delta_1d": r.get("shares_delta_1d"),
            "shares_delta_3d": r.get("shares_delta_3d"),
            "active_shares_delta_1d": r.get("active_shares_delta_1d"),
            "active_shares_delta_pct_1d": r.get("active_shares_delta_pct_1d"),
            "position_change_type": r.get("position_change_type"),
            "active_direction": r.get("active_direction"),
            "flow_adjusted_direction": r.get("flow_adjusted_direction"),
            "consecutive_add_days": r.get("consecutive_add_days"),
            "consecutive_reduce_days": r.get("consecutive_reduce_days"),
            "consecutive_active_add_days": r.get("consecutive_active_add_days"),
            "consecutive_active_reduce_days": r.get("consecutive_active_reduce_days"),
        }
        for r in rows
    ]


def _signal(date, signal_type, strength, score, row, rows):
    etfs = sorted({r["etf_code"] for r in rows})
    issuers = sorted(_issuers(rows))
    sid = f"{date}:{signal_type}:{row['stock_code']}:{'-'.join(etfs)}"
    return {
        "date": date,
        "signal_id": sid,
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
        "created_at": datetime.now().isoformat(),
    }


def _single_signals(date, rows):
    signals = []
    for r in rows:
        if r["is_new_position"] and _gte(r["weight_pct"], 2.0):
            strength = "strong" if _gte(r["weight_pct"], 3.0) and r.get("rank") is not None and r["rank"] <= 15 else "medium"
            signals.append(_signal(date, "new_core_position", strength, 4, r, [r]))
        if r["is_removed_position"] and _gte(r["prev_weight_pct"], 2.0):
            strength = "strong" if _gte(r["prev_weight_pct"], 3.0) and r.get("prev_rank") is not None and r["prev_rank"] <= 15 else "medium"
            signals.append(_signal(date, "removed_core_position", strength, -5, r, [r]))
        if _consecutive_active_add_event(r):
            score = 3 + (1 if _positive(_active_delta(r)) else 0)
            signals.append(_signal(date, "consecutive_add_3d", "medium", score, r, [r]))
        if _consecutive_active_reduce_event(r):
            score = -3 + (-1 if _negative(_active_delta(r)) else 0)
            signals.append(_signal(date, "consecutive_reduce_3d", "medium", score, r, [r]))
    return signals


def _consensus(date):
    rows = _recent(date, 3)
    out = []
    for signal_type, predicate in (("consensus_add_3d", _is_add), ("consensus_reduce_3d", _is_reduce)):
        grouped = {}
        for r in rows:
            if predicate(r):
                grouped.setdefault(r["stock_code"], []).append(r)
        for events in grouped.values():
            issuer_count = len(_issuers(events))
            if issuer_count < 2:
                continue
            rep = sorted(events, key=lambda x: (x["date"], x["etf_code"]))[-1]
            strong = issuer_count >= 3
            score = 6 if strong else 4
            if signal_type == "consensus_reduce_3d":
                score = -score
            out.append(_signal(date, signal_type, "strong" if strong else "medium", score, rep, events))
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
                    confidence, created_at
                ) VALUES (
                    :date, :signal_id, :signal_type, :signal_strength,
                    :signal_score, :stock_code, :stock_name, :etf_codes,
                    :issuers, :etf_count, :issuer_count, :explanation,
                    :evidence_json, :action_label, :confidence, :created_at
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
    unique = {s["signal_id"]: s for s in signals}
    _replace(date, list(unique.values()))
    return {"ok": True, "date": date, "signals": len(unique)}
