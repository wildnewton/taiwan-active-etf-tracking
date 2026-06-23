import json
from datetime import datetime
from typing import Optional

import db
from changes import get_latest_valid_date


SCORE_MAP = {
    "new_core_position": 4,
    "removed_core_position": -5,
    "consecutive_add_3d": 3,
    "consecutive_reduce_3d": -3,
}


def ensure_manager_signal_table() -> None:
    with db._connect() as conn:
        with conn:
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
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_manager_signals_date_type
                ON etf_manager_signals(date, signal_type)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_manager_signals_stock_date
                ON etf_manager_signals(stock_code, date)
                """
            )


def generate_manager_signals(signal_date: Optional[str] = None) -> dict:
    ensure_manager_signal_table()
    signal_date = signal_date or get_latest_valid_date()
    if not signal_date:
        return {"ok": False, "date": None, "signals": 0, "reason": "no signal date"}

    rows = _load_changes_for_date(signal_date)
    if not rows:
        _replace_signals(signal_date, [])
        return {"ok": True, "date": signal_date, "signals": 0}

    signals = []
    signals.extend(_single_etf_signals(signal_date, rows))
    signals.extend(_consensus_signals(signal_date))
    signals = _dedupe_signals(signals)
    _replace_signals(signal_date, signals)

    return {"ok": True, "date": signal_date, "signals": len(signals)}


def score_to_action_label(score: float) -> str:
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


def _load_changes_for_date(date_value: str) -> list[dict]:
    with db._connect() as conn:
        conn.row_factory = _dict_factory
        return conn.execute(
            "SELECT * FROM etf_holding_changes WHERE date = ? ORDER BY stock_code, etf_code",
            (date_value,),
        ).fetchall()


def _load_recent_changes(signal_date: str, lookback_days: int = 3) -> list[dict]:
    with db._connect() as conn:
        dates = [
            row[0]
            for row in conn.execute(
                """
                SELECT DISTINCT date
                FROM etf_holding_changes
                WHERE date <= ?
                ORDER BY date DESC
                LIMIT ?
                """,
                (signal_date, lookback_days),
            ).fetchall()
        ]
        if not dates:
            return []
        conn.row_factory = _dict_factory
        placeholders = ",".join("?" for _ in dates)
        return conn.execute(
            f"SELECT * FROM etf_holding_changes WHERE date IN ({placeholders}) ORDER BY date, stock_code, etf_code",
            dates,
        ).fetchall()


def _single_etf_signals(signal_date: str, rows: list[dict]) -> list[dict]:
    signals = []
    for row in rows:
        if row["is_new_position"] and _gte(row["weight_pct"], 2.0):
            score = SCORE_MAP["new_core_position"] + _rank_modifier(row)
            strength = "strong" if _gte(row["weight_pct"], 3.0) and _rank_at_most(row.get("rank"), 15) else "medium"
            signals.append(_make_signal(signal_date, "new_core_position", strength, score, row, [row]))

        if row["is_removed_position"] and _gte(row["prev_weight_pct"], 2.0):
            score = SCORE_MAP["removed_core_position"] + _rank_modifier(row)
            strength = "strong" if _gte(row["prev_weight_pct"], 3.0) and _rank_at_most(row.get("prev_rank"), 15) else "medium"
            signals.append(_make_signal(signal_date, "removed_core_position", strength, score, row, [row]))

        if row["consecutive_add_days"] >= 3 and _gte(row["weight_delta_3d"], 0.8):
            score = SCORE_MAP["consecutive_add_3d"] + _share_modifier(row, positive=True)
            signals.append(_make_signal(signal_date, "consecutive_add_3d", "medium", score, row, [row]))

        if row["consecutive_reduce_days"] >= 3 and _lte(row["weight_delta_3d"], -0.8):
            score = SCORE_MAP["consecutive_reduce_3d"] + _share_modifier(row, positive=False)
            signals.append(_make_signal(signal_date, "consecutive_reduce_3d", "medium", score, row, [row]))

    return signals


def _consensus_signals(signal_date: str) -> list[dict]:
    rows = _load_recent_changes(signal_date, lookback_days=3)
    add_events = {}
    reduce_events = {}
    for row in rows:
        if _is_add_event(row):
            add_events.setdefault(row["stock_code"], []).append(row)
        if _is_reduce_event(row):
            reduce_events.setdefault(row["stock_code"], []).append(row)

    signals = []
    for stock_code, events in add_events.items():
        issuers = _issuer_set(events)
        if len(issuers) < 2:
            continue
        score = 6 if len(issuers) >= 3 else 4
        strength = "strong" if len(issuers) >= 3 else "medium"
        signals.append(_make_signal(signal_date, "consensus_add_3d", strength, score, _representative(events), events))

    for stock_code, events in reduce_events.items():
        issuers = _issuer_set(events)
        if len(issuers) < 2:
            continue
        score = -6 if len(issuers) >= 3 else -4
        strength = "strong" if len(issuers) >= 3 else "medium"
        signals.append(_make_signal(signal_date, "consensus_reduce_3d", strength, score, _representative(events), events))

    return signals


def _is_add_event(row: dict) -> bool:
    return (row["is_new_position"] and _gte(row["weight_pct"], 2.0)) or (
        row["consecutive_add_days"] >= 3 and _gte(row["weight_delta_3d"], 0.8)
    )


def _is_reduce_event(row: dict) -> bool:
    return (row["is_removed_position"] and _gte(row["prev_weight_pct"], 2.0)) or (
        row["consecutive_reduce_days"] >= 3 and _lte(row["weight_delta_3d"], -0.8)
    )


def _make_signal(date_value: str, signal_type: str, strength: str, score: float, row: dict, evidence_rows: list[dict]) -> dict:
    etf_codes = sorted({event["etf_code"] for event in evidence_rows})
    issuers = sorted(_issuer_set(evidence_rows))
    signal_id = f"{date_value}:{signal_type}:{row['stock_code']}:{'-'.join(etf_codes)}"
    evidence = [_evidence_item(event) for event in evidence_rows]
    return {
        "date": date_value,
        "signal_id": signal_id,
        "signal_type": signal_type,
        "signal_strength": strength,
        "signal_score": score,
        "stock_code": row["stock_code"],
        "stock_name": row.get("stock_name"),
        "etf_codes": json.dumps(etf_codes, ensure_ascii=False),
        "issuers": json.dumps(issuers, ensure_ascii=False),
        "etf_count": len(etf_codes),
        "issuer_count": len(issuers),
        "explanation": _explanation(signal_type, row, evidence_rows),
        "evidence_json": json.dumps(evidence, ensure_ascii=False),
        "action_label": score_to_action_label(score),
        "confidence": "normal",
        "created_at": datetime.now().isoformat(),
    }


def _explanation(signal_type: str, row: dict, evidence_rows: list[dict]) -> str:
    if signal_type.startswith("consensus"):
        return f"{row['stock_code']} has {signal_type} evidence from {len(_issuer_set(evidence_rows))} issuers."
    return f"{row['stock_code']} generated {signal_type} in {row['etf_code']}."


def _evidence_item(row: dict) -> dict:
    return {
        "date": row.get("date"),
        "etf_code": row.get("etf_code"),
        "issuer": row.get("issuer"),
        "stock_code": row.get("stock_code"),
        "stock_name": row.get("stock_name"),
        "weight_pct": row.get("weight_pct"),
        "prev_weight_pct": row.get("prev_weight_pct"),
        "weight_delta_3d": row.get("weight_delta_3d"),
        "shares_delta_1d": row.get("shares_delta_1d"),
        "consecutive_add_days": row.get("consecutive_add_days"),
        "consecutive_reduce_days": row.get("consecutive_reduce_days"),
    }


def _replace_signals(date_value: str, signals: list[dict]) -> None:
    with db._connect() as conn:
        with conn:
            conn.execute("DELETE FROM etf_manager_signals WHERE date = ?", (date_value,))
            if not signals:
                return
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


def _dedupe_signals(signals: list[dict]) -> list[dict]:
    by_id = {}
    for signal in signals:
        by_id[signal["signal_id"]] = signal
    return list(by_id.values())


def _issuer_set(rows: list[dict]) -> set[str]:
    return {row["issuer"] for row in rows if row.get("issuer")}


def _representative(rows: list[dict]) -> dict:
    return sorted(rows, key=lambda row: (row["date"], row["etf_code"]))[-1]


def _rank_modifier(row: dict) -> int:
    rank_delta = row.get("rank_delta_1d")
    if rank_delta is not None and rank_delta >= 5:
        return 1
    if rank_delta is not None and rank_delta <= -5:
        return -1
    return 0


def _share_modifier(row: dict, positive: bool) -> int:
    delta = row.get("shares_delta_1d")
    if delta is None:
        return 0
    if positive and delta > 0:
        return 1
    if not positive and delta < 0:
        return -1
    return 0


def _rank_at_most(value, threshold: int) -> bool:
    return value is not None and value <= threshold


def _gte(value, threshold: float) -> bool:
    return value is not None and value >= threshold


def _lte(value, threshold: float) -> bool:
    return value is not None and value <= threshold


def _dict_factory(cursor, row):
    return {column[0]: row[index] for index, column in enumerate(cursor.description)}
