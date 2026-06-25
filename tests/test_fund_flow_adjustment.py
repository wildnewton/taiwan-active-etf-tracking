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
                'moneydj_primary', 'test', '2026-06-25T00:00:00')
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


def signal_types():
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


def seed_previous_day(date="2026-06-23"):
    insert_holding(date, "00980A", "2330", "台積電", 100, 10.0)
    insert_holding(date, "00980A", "2308", "台達電", 100, 8.0)
    insert_holding(date, "00980A", "2454", "聯發科", 100, 6.0)
    insert_holding(date, "00980A", "2383", "台光電", 100, 4.0)
    insert_holding(date, "00980A", "2345", "智邦", 100, 3.0)


def test_proportional_share_increase_is_flow_scaled_not_active_add():
    db.init_db(":memory:")
    seed_previous_day()
    for code, name, weight in [
        ("2330", "台積電", 10.0),
        ("2308", "台達電", 8.0),
        ("2454", "聯發科", 6.0),
        ("2383", "台光電", 4.0),
        ("2345", "智邦", 3.0),
    ]:
        insert_holding("2026-06-24", "00980A", code, name, 110, weight)

    summary = detect_holding_changes("2026-06-24", "2026-06-23")

    assert summary["ok"] is True
    tsmc = fetch_change("2330")
    assert round(tsmc["etf_scale_factor"], 4) == 1.1
    assert round(tsmc["expected_shares"], 4) == 110.0
    assert round(tsmc["active_shares_delta_1d"], 4) == 0.0
    assert abs(tsmc["active_shares_delta_pct_1d"]) < 0.0001
    assert tsmc["position_change_type"] == "flow_scaled_increase"
    assert tsmc["is_flow_scaled_change"] == 1
    assert tsmc["is_active_add"] == 0

    generate_manager_signals("2026-06-24")
    assert "consecutive_add_3d" not in signal_types()


def test_proportional_share_decrease_is_flow_scaled_not_active_reduce():
    db.init_db(":memory:")
    seed_previous_day()
    for code, name, weight in [
        ("2330", "台積電", 10.0),
        ("2308", "台達電", 8.0),
        ("2454", "聯發科", 6.0),
        ("2383", "台光電", 4.0),
        ("2345", "智邦", 3.0),
    ]:
        insert_holding("2026-06-24", "00980A", code, name, 90, weight)

    detect_holding_changes("2026-06-24", "2026-06-23")

    row = fetch_change("2330")
    assert round(row["etf_scale_factor"], 4) == 0.9
    assert round(row["expected_shares"], 4) == 90.0
    assert round(row["active_shares_delta_1d"], 4) == 0.0
    assert row["position_change_type"] == "flow_scaled_decrease"
    assert row["is_flow_scaled_change"] == 1
    assert row["is_active_reduce"] == 0


def test_excess_increase_above_etf_scale_is_confirmed_active_add():
    db.init_db(":memory:")
    seed_previous_day()
    insert_holding("2026-06-24", "00980A", "2330", "台積電", 130, 11.0)
    insert_holding("2026-06-24", "00980A", "2308", "台達電", 110, 8.0)
    insert_holding("2026-06-24", "00980A", "2454", "聯發科", 110, 6.0)
    insert_holding("2026-06-24", "00980A", "2383", "台光電", 110, 4.0)
    insert_holding("2026-06-24", "00980A", "2345", "智邦", 110, 3.0)

    detect_holding_changes("2026-06-24", "2026-06-23")

    tsmc = fetch_change("2330")
    assert round(tsmc["etf_scale_factor"], 4) == 1.1
    assert round(tsmc["expected_shares"], 4) == 110.0
    assert round(tsmc["active_shares_delta_1d"], 4) == 20.0
    assert round(tsmc["active_shares_delta_pct_1d"], 4) == round(20 / 110 * 100, 4)
    assert tsmc["position_change_type"] == "confirmed_active_add"
    assert tsmc["flow_adjusted_direction"] == "add"
    assert tsmc["is_active_add"] == 1


def test_underweight_after_etf_scale_is_confirmed_active_reduce_even_if_raw_shares_flat():
    db.init_db(":memory:")
    seed_previous_day()
    insert_holding("2026-06-24", "00980A", "2330", "台積電", 100, 9.0)
    insert_holding("2026-06-24", "00980A", "2308", "台達電", 110, 8.0)
    insert_holding("2026-06-24", "00980A", "2454", "聯發科", 110, 6.0)
    insert_holding("2026-06-24", "00980A", "2383", "台光電", 110, 4.0)
    insert_holding("2026-06-24", "00980A", "2345", "智邦", 110, 3.0)

    detect_holding_changes("2026-06-24", "2026-06-23")

    tsmc = fetch_change("2330")
    assert round(tsmc["etf_scale_factor"], 4) == 1.1
    assert round(tsmc["expected_shares"], 4) == 110.0
    assert round(tsmc["active_shares_delta_1d"], 4) == -10.0
    assert tsmc["position_change_type"] == "confirmed_active_reduce"
    assert tsmc["flow_adjusted_direction"] == "reduce"
    assert tsmc["is_active_reduce"] == 1


def test_scale_factor_uses_median_and_ignores_outlier_ratio():
    db.init_db(":memory:")
    seed_previous_day()
    insert_holding("2026-06-24", "00980A", "2330", "台積電", 200, 12.0)
    insert_holding("2026-06-24", "00980A", "2308", "台達電", 110, 8.0)
    insert_holding("2026-06-24", "00980A", "2454", "聯發科", 110, 6.0)
    insert_holding("2026-06-24", "00980A", "2383", "台光電", 110, 4.0)
    insert_holding("2026-06-24", "00980A", "2345", "智邦", 110, 3.0)

    detect_holding_changes("2026-06-24", "2026-06-23")

    row = fetch_change("2308")
    assert round(row["etf_scale_factor"], 4) == 1.1
    assert round(row["active_shares_delta_1d"], 4) == 0.0
    outlier = fetch_change("2330")
    assert round(outlier["active_shares_delta_1d"], 4) == 90.0


def test_too_few_comparable_shares_disables_flow_adjustment():
    db.init_db(":memory:")
    insert_holding("2026-06-23", "00980A", "2330", "台積電", 100, 10.0)
    insert_holding("2026-06-24", "00980A", "2330", "台積電", 110, 11.0)

    detect_holding_changes("2026-06-24", "2026-06-23")

    row = fetch_change("2330")
    assert row["etf_scale_factor"] is None
    assert row["expected_shares"] is None
    assert row["active_shares_delta_1d"] == 10
    assert row["position_change_type"] == "confirmed_active_add"
