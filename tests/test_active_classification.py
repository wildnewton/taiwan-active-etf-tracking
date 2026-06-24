import sqlite3

import db
from changes import detect_holding_changes
from signals import generate_manager_signals


def insert_holding(date, etf_code, stock_code, stock_name, shares, weight_pct):
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_daily_holdings (
                date, etf_code, asset_name, asset_type, stock_code, stock_name,
                shares, weight_pct, source_url, source_type, extraction_method,
                scraped_at
            ) VALUES (?, ?, ?, 'stock', ?, ?, ?, ?, 'https://example.test',
                'moneydj_primary', 'test', '2026-06-24T00:00:00')
            """,
            (
                date,
                etf_code,
                f"{stock_name}({stock_code}.TW)",
                stock_code,
                stock_name,
                shares,
                weight_pct,
            ),
        )


def fetch_change(stock_code, etf_code="00980A", date="2026-06-24"):
    conn = db._connect()
    old_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            """
            SELECT *
            FROM etf_holding_changes
            WHERE date = ? AND etf_code = ? AND stock_code = ?
            """,
            (date, etf_code, stock_code),
        ).fetchone()
    finally:
        conn.row_factory = old_factory


def load_signal_types():
    conn = db._connect()
    old_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return [
            row["signal_type"]
            for row in conn.execute(
                "SELECT signal_type FROM etf_manager_signals ORDER BY signal_type"
            ).fetchall()
        ]
    finally:
        conn.row_factory = old_factory


def insert_change(
    *,
    stock_code,
    issuer,
    etf_code,
    position_change_type,
    active_direction,
    is_active_add=0,
    is_active_reduce=0,
    is_passive_weight_change=0,
    is_mixed_weight_share_signal=0,
    weight_delta_3d=0.0,
    shares_delta_3d=0.0,
    consecutive_active_add_days=0,
    consecutive_active_reduce_days=0,
    consecutive_add_days=0,
    consecutive_reduce_days=0,
):
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_holding_changes (
                date, etf_code, issuer, stock_code, stock_name,
                prev_date, prev_weight_pct, weight_pct, weight_delta_1d,
                weight_delta_pct_1d, prev_shares, shares, shares_delta_1d,
                shares_delta_pct_1d, prev_rank, rank, rank_delta_1d,
                is_new_position, is_removed_position, weight_delta_3d,
                weight_delta_5d, weight_delta_10d, shares_delta_3d,
                shares_delta_5d, shares_delta_10d, consecutive_add_days,
                consecutive_reduce_days, consecutive_active_add_days,
                consecutive_active_reduce_days, position_change_type,
                active_direction, active_delta_source, is_active_add,
                is_active_reduce, is_passive_weight_change,
                is_mixed_weight_share_signal, confidence, source_type, created_at
            ) VALUES (
                '2026-06-24', ?, ?, ?, '測試股', '2026-06-23',
                2.0, 2.2, 0.2, NULL, 100.0, 100.0, 0.0,
                NULL, 5, 5, 0, 0, 0, ?, NULL, NULL, ?, NULL, NULL,
                ?, ?, ?, ?, ?, ?, 'shares', ?, ?, ?, ?, 'normal',
                'moneydj_primary', '2026-06-24T00:00:00'
            )
            """,
            (
                etf_code,
                issuer,
                stock_code,
                weight_delta_3d,
                shares_delta_3d,
                consecutive_add_days,
                consecutive_reduce_days,
                consecutive_active_add_days,
                consecutive_active_reduce_days,
                position_change_type,
                active_direction,
                is_active_add,
                is_active_reduce,
                is_passive_weight_change,
                is_mixed_weight_share_signal,
            ),
        )


def test_weight_only_increase_is_passive_not_active_add():
    db.init_db(":memory:")
    insert_holding("2026-06-23", "00980A", "2330", "台積電", 100, 5.0)
    insert_holding("2026-06-24", "00980A", "2330", "台積電", 100, 6.0)

    detect_holding_changes("2026-06-24", "2026-06-23")

    row = fetch_change("2330")
    assert row["shares_delta_1d"] == 0
    assert row["weight_delta_1d"] == 1.0
    assert row["position_change_type"] == "passive_weight_increase"
    assert row["active_direction"] == "none"
    assert row["is_active_add"] == 0
    assert row["is_passive_weight_change"] == 1
    assert row["consecutive_active_add_days"] == 0


def test_weight_only_decrease_is_passive_not_active_reduce():
    db.init_db(":memory:")
    insert_holding("2026-06-23", "00980A", "2330", "台積電", 100, 6.0)
    insert_holding("2026-06-24", "00980A", "2330", "台積電", 100, 5.0)

    detect_holding_changes("2026-06-24", "2026-06-23")

    row = fetch_change("2330")
    assert row["shares_delta_1d"] == 0
    assert row["weight_delta_1d"] == -1.0
    assert row["position_change_type"] == "passive_weight_decrease"
    assert row["active_direction"] == "none"
    assert row["is_active_reduce"] == 0
    assert row["is_passive_weight_change"] == 1
    assert row["consecutive_active_reduce_days"] == 0


def test_mixed_share_and_weight_direction_keeps_active_direction_but_marks_mixed():
    db.init_db(":memory:")
    insert_holding("2026-06-23", "00980A", "2330", "台積電", 100, 6.0)
    insert_holding("2026-06-24", "00980A", "2330", "台積電", 110, 5.0)
    insert_holding("2026-06-23", "00980A", "2454", "聯發科", 100, 5.0)
    insert_holding("2026-06-24", "00980A", "2454", "聯發科", 90, 6.0)

    detect_holding_changes("2026-06-24", "2026-06-23")

    add_mixed = fetch_change("2330")
    assert add_mixed["position_change_type"] == "mixed_add_but_weight_down"
    assert add_mixed["active_direction"] == "add"
    assert add_mixed["is_active_add"] == 1
    assert add_mixed["is_mixed_weight_share_signal"] == 1

    reduce_mixed = fetch_change("2454")
    assert reduce_mixed["position_change_type"] == "mixed_reduce_but_weight_up"
    assert reduce_mixed["active_direction"] == "reduce"
    assert reduce_mixed["is_active_reduce"] == 1
    assert reduce_mixed["is_mixed_weight_share_signal"] == 1


def test_consecutive_active_days_use_shares_not_weight():
    db.init_db(":memory:")
    # Weight rises every day, but shares are unchanged. This is not active adding.
    insert_holding("2026-06-22", "00980A", "2330", "台積電", 100, 4.0)
    insert_holding("2026-06-23", "00980A", "2330", "台積電", 100, 5.0)
    insert_holding("2026-06-24", "00980A", "2330", "台積電", 100, 6.0)

    detect_holding_changes("2026-06-24", "2026-06-23")

    row = fetch_change("2330")
    assert row["consecutive_add_days"] == 2
    assert row["consecutive_active_add_days"] == 0
    assert row["shares_delta_3d"] == 0


def test_signals_ignore_passive_weight_only_consensus_events():
    db.init_db(":memory:")
    insert_change(
        stock_code="2330",
        issuer="Nomura",
        etf_code="00980A",
        position_change_type="passive_weight_increase",
        active_direction="none",
        is_passive_weight_change=1,
        weight_delta_3d=1.2,
        shares_delta_3d=0.0,
        consecutive_add_days=3,
        consecutive_active_add_days=0,
    )
    insert_change(
        stock_code="2330",
        issuer="Uni-President",
        etf_code="00981A",
        position_change_type="passive_weight_increase",
        active_direction="none",
        is_passive_weight_change=1,
        weight_delta_3d=1.1,
        shares_delta_3d=0.0,
        consecutive_add_days=3,
        consecutive_active_add_days=0,
    )

    generate_manager_signals("2026-06-24")

    assert "consecutive_add_3d" not in load_signal_types()
    assert "consensus_add_3d" not in load_signal_types()


def test_signals_use_confirmed_active_consensus_events():
    db.init_db(":memory:")
    insert_change(
        stock_code="2330",
        issuer="Nomura",
        etf_code="00980A",
        position_change_type="confirmed_active_add",
        active_direction="add",
        is_active_add=1,
        weight_delta_3d=0.2,
        shares_delta_3d=30.0,
        consecutive_active_add_days=3,
    )
    insert_change(
        stock_code="2330",
        issuer="Uni-President",
        etf_code="00981A",
        position_change_type="confirmed_active_add",
        active_direction="add",
        is_active_add=1,
        weight_delta_3d=-0.1,
        shares_delta_3d=20.0,
        consecutive_active_add_days=3,
    )

    generate_manager_signals("2026-06-24")

    signal_types = load_signal_types()
    assert "consecutive_add_3d" in signal_types
    assert "consensus_add_3d" in signal_types
