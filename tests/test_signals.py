import json
import sqlite3

import db
from signals import generate_manager_signals, score_to_action_label


def insert_change(
    date="2026-06-23",
    etf_code="00980A",
    issuer="Nomura",
    stock_code="2330",
    stock_name="台積電",
    prev_weight_pct=1.0,
    weight_pct=1.0,
    prev_shares=100.0,
    shares=100.0,
    prev_rank=3,
    rank=3,
    is_new_position=0,
    is_removed_position=0,
    weight_delta_1d=0.0,
    weight_delta_3d=None,
    consecutive_add_days=0,
    consecutive_reduce_days=0,
):
    shares_delta = None
    if shares is not None and prev_shares is not None:
        shares_delta = shares - prev_shares

    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_holding_changes (
                date, etf_code, issuer, stock_code, stock_name,
                prev_date, prev_weight_pct, weight_pct, weight_delta_1d,
                weight_delta_pct_1d, prev_shares, shares, shares_delta_1d,
                shares_delta_pct_1d, prev_rank, rank, rank_delta_1d,
                is_new_position, is_removed_position, weight_delta_3d,
                weight_delta_5d, weight_delta_10d, consecutive_add_days,
                consecutive_reduce_days, source_type, created_at
            ) VALUES (?, ?, ?, ?, ?, '2026-06-22', ?, ?, ?, NULL, ?, ?, ?,
                NULL, ?, ?, NULL, ?, ?, ?, NULL, NULL, ?, ?, 'moneydj_primary',
                '2026-06-23T00:00:00')
            """,
            (
                date,
                etf_code,
                issuer,
                stock_code,
                stock_name,
                prev_weight_pct,
                weight_pct,
                weight_delta_1d,
                prev_shares,
                shares,
                shares_delta,
                prev_rank,
                rank,
                is_new_position,
                is_removed_position,
                weight_delta_3d,
                consecutive_add_days,
                consecutive_reduce_days,
            ),
        )


def load_signals():
    with db._connect() as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT * FROM etf_manager_signals ORDER BY signal_type, stock_code, etf_codes"
        ).fetchall()


def test_score_to_action_label_boundaries():
    assert score_to_action_label(8) == "Strong Watch"
    assert score_to_action_label(4) == "Watch"
    assert score_to_action_label(1) == "Mild Positive"
    assert score_to_action_label(0) == "Neutral"
    assert score_to_action_label(-1) == "Mild Negative"
    assert score_to_action_label(-4) == "Reduce Watch"
    assert score_to_action_label(-8) == "Strong Reduce Watch"


def test_generates_new_and_removed_core_position_signals():
    db.init_db(":memory:")
    insert_change(
        stock_code="6669",
        stock_name="緯穎",
        prev_weight_pct=None,
        weight_pct=3.2,
        prev_shares=None,
        shares=50,
        prev_rank=None,
        rank=10,
        is_new_position=1,
        weight_delta_1d=3.2,
    )
    insert_change(
        stock_code="2383",
        stock_name="台光電",
        prev_weight_pct=3.5,
        weight_pct=0.0,
        prev_shares=100,
        shares=None,
        prev_rank=8,
        rank=None,
        is_removed_position=1,
        weight_delta_1d=-3.5,
    )
    insert_change(
        stock_code="2308",
        stock_name="台達電",
        prev_weight_pct=None,
        weight_pct=1.9,
        prev_shares=None,
        shares=20,
        prev_rank=None,
        rank=20,
        is_new_position=1,
        weight_delta_1d=1.9,
    )

    summary = generate_manager_signals("2026-06-23")
    signals = load_signals()

    assert summary["ok"] is True
    assert {row["signal_type"] for row in signals} == {
        "new_core_position",
        "removed_core_position",
    }
    new_core = [row for row in signals if row["signal_type"] == "new_core_position"][0]
    removed = [row for row in signals if row["signal_type"] == "removed_core_position"][0]
    assert new_core["stock_code"] == "6669"
    assert new_core["signal_strength"] == "strong"
    assert new_core["signal_score"] == 4
    assert new_core["action_label"] == "Watch"
    assert removed["stock_code"] == "2383"
    assert removed["signal_strength"] == "strong"
    assert removed["signal_score"] == -5
    assert removed["action_label"] == "Reduce Watch"


def test_generates_consecutive_add_and_reduce_signals_with_share_confirmation():
    db.init_db(":memory:")
    insert_change(
        stock_code="2330",
        stock_name="台積電",
        prev_weight_pct=5.0,
        weight_pct=6.0,
        prev_shares=100,
        shares=120,
        weight_delta_1d=1.0,
        weight_delta_3d=1.2,
        consecutive_add_days=3,
    )
    insert_change(
        stock_code="2454",
        stock_name="聯發科",
        prev_weight_pct=5.0,
        weight_pct=4.0,
        prev_shares=100,
        shares=90,
        weight_delta_1d=-1.0,
        weight_delta_3d=-1.1,
        consecutive_reduce_days=3,
    )

    generate_manager_signals("2026-06-23")
    signals = load_signals()

    add = [row for row in signals if row["signal_type"] == "consecutive_add_3d"][0]
    reduce = [row for row in signals if row["signal_type"] == "consecutive_reduce_3d"][0]
    assert add["signal_score"] == 4
    assert add["action_label"] == "Watch"
    assert reduce["signal_score"] == -4
    assert reduce["action_label"] == "Reduce Watch"


def test_consensus_add_requires_two_independent_issuers_not_two_etfs_same_issuer():
    db.init_db(":memory:")
    insert_change(
        etf_code="00980A",
        issuer="Nomura",
        stock_code="2383",
        stock_name="台光電",
        prev_weight_pct=1.0,
        weight_pct=3.0,
        is_new_position=1,
        weight_delta_1d=2.0,
    )
    insert_change(
        etf_code="00985A",
        issuer="Nomura",
        stock_code="2383",
        stock_name="台光電",
        prev_weight_pct=1.0,
        weight_pct=2.5,
        is_new_position=1,
        weight_delta_1d=1.5,
    )

    generate_manager_signals("2026-06-23")
    signals = load_signals()
    assert "consensus_add_3d" not in {row["signal_type"] for row in signals}

    insert_change(
        etf_code="00981A",
        issuer="Uni-President",
        stock_code="2383",
        stock_name="台光電",
        prev_weight_pct=1.0,
        weight_pct=2.1,
        is_new_position=1,
        weight_delta_1d=1.1,
    )

    generate_manager_signals("2026-06-23")
    consensus = [row for row in load_signals() if row["signal_type"] == "consensus_add_3d"]
    assert len(consensus) == 1
    row = consensus[0]
    assert row["stock_code"] == "2383"
    assert row["issuer_count"] == 2
    assert row["etf_count"] == 3
    assert row["signal_score"] == 4
    assert json.loads(row["issuers"]) == ["Nomura", "Uni-President"]


def test_consensus_reduce_three_issuers_is_strong():
    db.init_db(":memory:")
    for etf_code, issuer in [
        ("00980A", "Nomura"),
        ("00981A", "Uni-President"),
        ("00405A", "Fubon"),
    ]:
        insert_change(
            etf_code=etf_code,
            issuer=issuer,
            stock_code="2454",
            stock_name="聯發科",
            prev_weight_pct=3.0,
            weight_pct=0.0,
            is_removed_position=1,
            weight_delta_1d=-3.0,
            prev_rank=5,
            rank=None,
        )

    generate_manager_signals("2026-06-23")
    consensus = [row for row in load_signals() if row["signal_type"] == "consensus_reduce_3d"]
    assert len(consensus) == 1
    row = consensus[0]
    assert row["signal_strength"] == "strong"
    assert row["issuer_count"] == 3
    assert row["signal_score"] == -6
    assert row["action_label"] == "Reduce Watch"
