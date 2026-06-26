import sqlite3

import db
from signals import generate_manager_signals


def insert_change(date, issuer, etf_code, direction="add", stock_code="2330"):
    is_add = 1 if direction == "add" else 0
    is_reduce = 1 if direction == "reduce" else 0
    active_direction = direction if direction in {"add", "reduce"} else "none"
    position_change_type = {
        "add": "confirmed_active_add",
        "reduce": "confirmed_active_reduce",
    }.get(direction, "unchanged")
    active_delta = 10.0 if direction == "add" else -10.0 if direction == "reduce" else 0.0
    shares_delta = active_delta
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_holding_changes (
                date, etf_code, issuer, stock_code, stock_name,
                prev_date, prev_weight_pct, weight_pct, weight_delta_1d,
                prev_shares, shares, shares_delta_1d,
                shares_delta_3d, active_shares_delta_1d,
                active_shares_delta_pct_1d, is_new_position,
                is_removed_position, consecutive_active_add_days,
                consecutive_active_reduce_days, position_change_type,
                active_direction, active_delta_source, is_active_add,
                is_active_reduce, is_passive_weight_change,
                is_mixed_weight_share_signal, confidence, source_type,
                created_at
            ) VALUES (
                ?, ?, ?, ?, '測試股', '2026-06-20',
                2.0, 2.2, 0.2, 100.0, 110.0, ?, ?, ?,
                2.0, 0, 0, 0, 0, ?, ?, 'flow_adjusted_shares',
                ?, ?, 0, 0, 'normal', 'moneydj_primary',
                '2026-06-25T00:00:00'
            )
            """,
            (
                date,
                etf_code,
                issuer,
                stock_code,
                shares_delta,
                shares_delta,
                active_delta,
                position_change_type,
                active_direction,
                is_add,
                is_reduce,
            ),
        )


def insert_neutral_date(date):
    insert_change(date, "Neutral", "00999A", direction="none", stock_code="9999")


def fetch_consensus(date, signal_type="consensus_add_3d", stock_code="2330"):
    conn = db._connect()
    old_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            """
            SELECT signal_type, signal_freshness, freshness_reason,
                   issuer_count, etf_count
            FROM etf_manager_signals
            WHERE date = ? AND signal_type = ? AND stock_code = ?
            """,
            (date, signal_type, stock_code),
        ).fetchone()
    finally:
        conn.row_factory = old_factory


def test_consensus_first_crossing_is_new():
    db.init_db(":memory:")
    insert_change("2026-06-23", "Nomura", "00980A", direction="add")
    insert_change("2026-06-24", "Uni-President", "00981A", direction="add")

    generate_manager_signals("2026-06-24")
    row = fetch_consensus("2026-06-24", "consensus_add_3d")

    assert row["signal_freshness"] == "new"
    assert "first reaches consensus" in row["freshness_reason"]


def test_consensus_with_current_event_and_prior_consensus_is_persistent():
    db.init_db(":memory:")
    insert_change("2026-06-22", "Nomura", "00980A", direction="add")
    insert_change("2026-06-22", "Uni-President", "00981A", direction="add")
    insert_change("2026-06-23", "Nomura", "00980A", direction="add")
    insert_change("2026-06-23", "Uni-President", "00981A", direction="add")

    generate_manager_signals("2026-06-23")
    row = fetch_consensus("2026-06-23", "consensus_add_3d")

    assert row["signal_freshness"] == "persistent"
    assert "continues" in row["freshness_reason"]


def test_consensus_with_declining_issuer_count_is_fading():
    db.init_db(":memory:")
    for issuer, etf_code in (
        ("Nomura", "00980A"),
        ("Uni-President", "00981A"),
        ("Cathay", "00982A"),
        ("Fubon", "00985A"),
    ):
        insert_change("2026-06-21", issuer, etf_code, direction="add")
    for date in ("2026-06-22", "2026-06-23", "2026-06-24"):
        insert_change(date, "Nomura", "00980A", direction="add")
        insert_change(date, "Uni-President", "00981A", direction="add")

    generate_manager_signals("2026-06-24")
    row = fetch_consensus("2026-06-24", "consensus_add_3d")

    assert row["signal_freshness"] == "fading"
    assert "issuer count declined" in row["freshness_reason"]


def test_consensus_without_current_day_event_is_stale():
    db.init_db(":memory:")
    insert_change("2026-06-21", "Nomura", "00980A", direction="add")
    insert_change("2026-06-21", "Uni-President", "00981A", direction="add")
    insert_neutral_date("2026-06-22")
    insert_neutral_date("2026-06-23")

    generate_manager_signals("2026-06-23")
    row = fetch_consensus("2026-06-23", "consensus_add_3d")

    assert row["signal_freshness"] == "stale"
    assert "no current-day event" in row["freshness_reason"]


def test_consensus_direction_reversal_is_labeled_reversal():
    db.init_db(":memory:")
    insert_change("2026-06-21", "Nomura", "00980A", direction="reduce")
    insert_change("2026-06-21", "Uni-President", "00981A", direction="reduce")
    insert_neutral_date("2026-06-22")
    insert_change("2026-06-23", "Nomura", "00980A", direction="add")
    insert_change("2026-06-23", "Uni-President", "00981A", direction="add")

    generate_manager_signals("2026-06-23")
    row = fetch_consensus("2026-06-23", "consensus_add_3d")

    assert row["signal_freshness"] == "reversal"
    assert "opposite consensus" in row["freshness_reason"]


def test_existing_signal_table_is_migrated_with_freshness_columns():
    db.init_db(":memory:")
    with db._connect() as conn:
        conn.execute(
            """
            CREATE TABLE etf_manager_signals (
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
    insert_change("2026-06-24", "Nomura", "00980A", direction="add")
    insert_change("2026-06-24", "Uni-President", "00981A", direction="add")

    generate_manager_signals("2026-06-24")
    row = fetch_consensus("2026-06-24", "consensus_add_3d")

    assert row["signal_freshness"] == "new"
