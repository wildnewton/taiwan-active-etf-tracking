import sqlite3

import db
from changes import detect_holding_changes
from signals import generate_manager_signals


BASE_HOLDINGS = [
    ("2308", "台達電", 8.0),
    ("2454", "聯發科", 6.0),
    ("2383", "台光電", 4.0),
    ("2345", "智邦", 3.0),
]


def insert_holding(date, stock_code, stock_name, shares, weight_pct, etf_code="00980A"):
    from etf_universe import upsert_etf

    upsert_etf({"code": etf_code, "name": f"ETF {etf_code}", "market": "TWSE"})
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
        stock_total = conn.execute(
            """
            SELECT COALESCE(SUM(weight_pct), 0.0)
            FROM etf_daily_holdings
            WHERE date = ? AND etf_code = ? AND source_type = 'moneydj_primary'
            """,
            (date, etf_code),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT OR REPLACE INTO etf_daily_non_stock_assets (
                date, etf_code, asset_name, asset_type, weight_pct,
                source_url, source_type, extraction_method, scraped_at
            ) VALUES (?, ?, 'Cash', 'cash', ?, 'https://example.test',
                      'moneydj_primary', 'test', '2026-06-25T00:00:00')
            """,
            (date, etf_code, 100.0 - stock_total),
        )


def seed_day(date, base_shares, tsmc_shares=None, tsmc_weight=10.0):
    insert_holding(date, "2330", "台積電", tsmc_shares if tsmc_shares is not None else base_shares, tsmc_weight)
    for code, name, weight in BASE_HOLDINGS:
        insert_holding(date, code, name, base_shares, weight)


def fetch_change(date="2026-06-24", stock_code="2330"):
    conn = db._connect()
    old_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            """
            SELECT *
            FROM etf_holding_changes
            WHERE date = ? AND etf_code = '00980A' AND stock_code = ?
            """,
            (date, stock_code),
        ).fetchone()
    finally:
        conn.row_factory = old_factory


def signal_types(date):
    conn = db._connect()
    old_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return [
            row["signal_type"]
            for row in conn.execute(
                "SELECT signal_type FROM etf_manager_signals WHERE date = ? ORDER BY signal_type",
                (date,),
            ).fetchall()
        ]
    finally:
        conn.row_factory = old_factory


def run_changes_for_four_days():
    detect_holding_changes("2026-06-22", "2026-06-21")
    detect_holding_changes("2026-06-23", "2026-06-22")
    detect_holding_changes("2026-06-24", "2026-06-23")


def test_three_day_proportional_scaling_does_not_create_consecutive_add_signal():
    db.init_db(":memory:")
    seed_day("2026-06-21", 100.0, tsmc_weight=10.0)
    seed_day("2026-06-22", 110.0, tsmc_weight=10.0)
    seed_day("2026-06-23", 121.0, tsmc_weight=10.0)
    seed_day("2026-06-24", 133.1, tsmc_weight=10.0)

    run_changes_for_four_days()
    row = fetch_change("2026-06-24", "2330")

    assert row["position_change_type"] == "flow_scaled_increase"
    assert row["is_flow_scaled_change"] == 1
    assert row["is_active_add"] == 0
    assert row["consecutive_active_add_days"] == 0

    generate_manager_signals("2026-06-24")
    assert "consecutive_add_3d" not in signal_types("2026-06-24")


def test_three_day_proportional_scaling_does_not_create_consecutive_reduce_signal():
    db.init_db(":memory:")
    seed_day("2026-06-21", 100.0, tsmc_weight=10.0)
    seed_day("2026-06-22", 90.0, tsmc_weight=10.0)
    seed_day("2026-06-23", 81.0, tsmc_weight=10.0)
    seed_day("2026-06-24", 72.9, tsmc_weight=10.0)

    run_changes_for_four_days()
    row = fetch_change("2026-06-24", "2330")

    assert row["position_change_type"] == "flow_scaled_decrease"
    assert row["is_flow_scaled_change"] == 1
    assert row["is_active_reduce"] == 0
    assert row["consecutive_active_reduce_days"] == 0

    generate_manager_signals("2026-06-24")
    assert "consecutive_reduce_3d" not in signal_types("2026-06-24")


def test_three_day_excess_flow_adjusted_add_creates_consecutive_add_signal():
    db.init_db(":memory:")
    seed_day("2026-06-21", 100.0, tsmc_shares=100.0, tsmc_weight=10.0)
    seed_day("2026-06-22", 110.0, tsmc_shares=130.0, tsmc_weight=11.0)
    seed_day("2026-06-23", 121.0, tsmc_shares=160.0, tsmc_weight=12.0)
    seed_day("2026-06-24", 133.1, tsmc_shares=190.0, tsmc_weight=13.0)

    run_changes_for_four_days()
    row = fetch_change("2026-06-24", "2330")

    assert row["position_change_type"] == "confirmed_active_add"
    assert row["flow_adjusted_direction"] == "add"
    assert row["is_active_add"] == 1
    assert row["consecutive_active_add_days"] == 3

    generate_manager_signals("2026-06-24")
    assert "consecutive_add_3d" in signal_types("2026-06-24")


def test_immaterial_flow_adjusted_increase_is_not_active_add():
    db.init_db(":memory:")
    seed_day("2026-06-23", 100.0, tsmc_shares=100.0, tsmc_weight=10.0)
    seed_day("2026-06-24", 110.0, tsmc_shares=110.5, tsmc_weight=10.1)

    detect_holding_changes("2026-06-24", "2026-06-23")
    row = fetch_change("2026-06-24", "2330")

    assert round(row["active_shares_delta_pct_1d"], 4) == round(0.5 / 110 * 100, 4)
    assert row["position_change_type"] == "immaterial_active_increase"
    assert row["is_active_add"] == 0
    assert row["flow_adjusted_direction"] == "none"

    generate_manager_signals("2026-06-24")
    assert signal_types("2026-06-24") == []


def test_material_flow_adjusted_increase_is_confirmed_active_add():
    db.init_db(":memory:")
    seed_day("2026-06-23", 100.0, tsmc_shares=100.0, tsmc_weight=10.0)
    seed_day("2026-06-24", 110.0, tsmc_shares=112.0, tsmc_weight=10.3)

    detect_holding_changes("2026-06-24", "2026-06-23")
    row = fetch_change("2026-06-24", "2330")

    assert round(row["active_shares_delta_pct_1d"], 4) == round(2.0 / 110 * 100, 4)
    assert row["position_change_type"] == "confirmed_active_add"
    assert row["is_active_add"] == 1
    assert row["flow_adjusted_direction"] == "add"


def test_materiality_threshold_does_not_block_new_or_removed_core_positions():
    db.init_db(":memory:")
    seed_day("2026-06-23", 100.0, tsmc_shares=100.0, tsmc_weight=10.0)
    seed_day("2026-06-24", 110.0, tsmc_shares=110.0, tsmc_weight=10.0)
    insert_holding("2026-06-24", "6669", "緯穎", 50.0, 3.2)

    detect_holding_changes("2026-06-24", "2026-06-23")
    new_row = fetch_change("2026-06-24", "6669")

    assert new_row["position_change_type"] == "new_position"
    assert new_row["is_active_add"] == 1

    generate_manager_signals("2026-06-24")
    assert "new_core_position" in signal_types("2026-06-24")
