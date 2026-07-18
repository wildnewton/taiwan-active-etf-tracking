import json
import math

import pytest

import db
from manager_intent import generate_manager_intent_rollups


ETF_ROWS = {
    "00980A": {"issuer": "野村", "retired": 0},
    "00981A": {"issuer": "野村", "retired": 0},
    "00982A": {"issuer": "統一", "retired": 0},
    "00984A": {"issuer": "台新", "retired": 0},
    "00985A": {"issuer": "群益", "retired": 0},
    "00998A": {"issuer": "歷史投信", "retired": 1},
    "00999A": {"issuer": "退休投信", "retired": 1},
}
ETF_ISSUERS = {etf_code: row["issuer"] for etf_code, row in ETF_ROWS.items()}
WINDOW_DATES = ["2026-06-22", "2026-06-23", "2026-06-24", "2026-06-25", "2026-06-26"]


@pytest.fixture(autouse=True)
def restore_default_db_after_test():
    yield
    if db._MEMORY_CONN is not None:
        db._MEMORY_CONN.close()
        db._MEMORY_CONN = None
    db._DB_PATH = db.DEFAULT_DB_PATH


def seed_universe():
    with db._connect() as conn:
        for etf_code, row in ETF_ROWS.items():
            conn.execute(
                """
                INSERT INTO etf_universe (
                    code, name, issuer, market, isin, retired,
                    first_seen_date, created_at, updated_at
                ) VALUES (?, ?, ?, 'TWSE', NULL, ?, '2026-06-01', ?, ?)
                """,
                (
                    etf_code,
                    f"Test {etf_code}",
                    row["issuer"],
                    row["retired"],
                    "2026-06-01T00:00:00",
                    "2026-06-01T00:00:00",
                ),
            )


def insert_holding(date, etf_code, stock_code="2330", stock_name="台積電", weight_pct=5.0):
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_daily_holdings (
                date, etf_code, asset_name, asset_type, stock_code, stock_name,
                shares, weight_pct, source_url, source_type, extraction_method,
                scraped_at
            ) VALUES (?, ?, ?, 'stock', ?, ?, 1000, ?, 'https://test', 'moneydj_primary', 'test', ?)
            """,
            (date, etf_code, f"{stock_name}({stock_code}.TW)", stock_code, stock_name, weight_pct, f"{date}T00:00:00"),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO etf_daily_non_stock_assets (
                date, etf_code, asset_name, asset_type, weight_pct,
                source_url, source_type, extraction_method, scraped_at
            ) VALUES (?, ?, 'Cash', 'cash', ?, 'https://test',
                      'moneydj_primary', 'test', ?)
            """,
            (
                date,
                etf_code,
                100.0 - weight_pct,
                f"{date}T00:00:00",
            ),
        )


def insert_comparable_context(date, etf_code):
    with db._connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO etf_change_diagnostics (
                date, prev_date, etf_code, status, reason,
                current_source_type, previous_source_type,
                current_stock_count, previous_stock_count,
                overlap_ratio, size_ratio, created_at
            ) VALUES (?, ?, ?, 'included', 'comparable_source_pair',
                'moneydj_primary', 'moneydj_primary', 10, 10, 1.0, 1.0, ?)
            """,
            (date, "2026-06-21", etf_code, f"{date}T00:00:00"),
        )


def insert_eligible_history(stock_code="2330", stock_name="台積電", etfs=("00980A",), dates=WINDOW_DATES):
    for date in dates:
        for etf_code in etfs:
            insert_holding(date, etf_code, stock_code, stock_name)
            insert_comparable_context(date, etf_code)


def insert_change(
    date,
    etf_code,
    stock_code="2330",
    stock_name="台積電",
    *,
    is_active_add=0,
    is_active_reduce=0,
    is_new_position=0,
    is_removed_position=0,
    position_change_type="confirmed_active_add",
    active_direction="add",
    active_delta=100.0,
    active_delta_pct=10.0,
    consecutive_active_add_days=0,
    consecutive_active_reduce_days=0,
):
    issuer = ETF_ISSUERS[etf_code]
    if is_active_reduce or is_removed_position:
        active_direction = "reduce"
        position_change_type = "removed_position" if is_removed_position else "confirmed_active_reduce"
        active_delta = -abs(active_delta) if active_delta is not None else None
        active_delta_pct = -abs(active_delta_pct) if active_delta_pct is not None else None
    elif is_new_position:
        active_direction = "add"
        position_change_type = "new_position"
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_holding_changes (
                date, etf_code, issuer, stock_code, stock_name, prev_date,
                prev_weight_pct, weight_pct, weight_delta_1d, prev_shares, shares,
                shares_delta_1d, active_shares_delta_1d, active_shares_delta_pct_1d,
                prev_rank, rank, is_new_position, is_removed_position,
                position_change_type, active_direction, is_active_add, is_active_reduce,
                consecutive_active_add_days, consecutive_active_reduce_days,
                confidence, source_type, created_at
            ) VALUES (?, ?, ?, ?, ?, '2026-06-21', 1.0, 2.0, 1.0, 1000, 1100,
                100, ?, ?, 30, 20, ?, ?, ?, ?, ?, ?, ?, ?, 'high', 'moneydj_primary', ?)
            """,
            (
                date,
                etf_code,
                issuer,
                stock_code,
                stock_name,
                active_delta,
                active_delta_pct,
                is_new_position,
                is_removed_position,
                position_change_type,
                active_direction,
                is_active_add,
                is_active_reduce,
                consecutive_active_add_days,
                consecutive_active_reduce_days,
                f"{date}T00:00:00",
            ),
        )


def get_rollup(*, date="2026-06-26", window_days=5, entity_level="stock", stock_code="2330", issuer_key=""):
    conn = db._connect()
    old_factory = conn.row_factory
    conn.row_factory = lambda cursor, row: {column[0]: row[index] for index, column in enumerate(cursor.description)}
    try:
        return conn.execute(
            """
            SELECT *
            FROM manager_intent_rollups
            WHERE date = ?
              AND window_days = ?
              AND entity_level = ?
              AND stock_code = ?
              AND issuer_key = ?
            """,
            (date, window_days, entity_level, stock_code, issuer_key),
        ).fetchone()
    finally:
        conn.row_factory = old_factory


def setup_db():
    db.init_db(":memory:")
    seed_universe()


def test_active_add_and_reduce_rows_create_buy_sell_net_and_gross_scores():
    setup_db()
    insert_eligible_history(etfs=("00980A", "00982A"))
    insert_change("2026-06-25", "00980A", is_active_add=1)
    insert_change("2026-06-26", "00982A", is_active_reduce=1)

    summary = generate_manager_intent_rollups("2026-06-26", windows=(5,))
    row = get_rollup()

    assert summary["rows"] > 0
    assert row["cum_active_buy_score"] == 2.0
    assert row["cum_active_sell_score"] == 2.0
    assert row["net_active_score"] == 0.0
    assert row["gross_active_score"] == 4.0
    assert row["net_to_gross"] == 0.0


def test_eligible_days_are_derived_from_comparable_context_not_action_rows_only():
    setup_db()
    insert_eligible_history(etfs=("00980A",))
    insert_change("2026-06-26", "00980A", is_active_add=1)

    generate_manager_intent_rollups("2026-06-26", windows=(5,))
    row = get_rollup(entity_level="issuer_stock", issuer_key="野村")

    assert row["eligible_days"] == 5
    assert row["buy_days"] == 1
    assert row["sell_days"] == 0
    assert math.isclose(row["buy_day_pct"], 0.2)
    assert math.isclose(row["sell_day_pct"], 0.0)


def test_issuer_buy_day_pct_does_not_overcount_multiple_etfs_on_same_day():
    setup_db()
    insert_eligible_history(etfs=("00980A", "00981A"))
    insert_change("2026-06-26", "00980A", is_active_add=1)
    insert_change("2026-06-26", "00981A", is_active_add=1)

    generate_manager_intent_rollups("2026-06-26", windows=(5,))
    row = get_rollup(entity_level="issuer_stock", issuer_key="野村")

    assert row["eligible_days"] == 5
    assert row["buy_days"] == 1
    assert math.isclose(row["buy_day_pct"], 0.2)
    assert row["buy_etf_count"] == 2
    assert row["buy_issuer_count"] == 1


def test_accumulation_classified_when_net_positive_and_breadth_exists():
    setup_db()
    insert_eligible_history(etfs=("00980A", "00982A"))
    insert_change("2026-06-25", "00980A", is_active_add=1)
    insert_change("2026-06-26", "00982A", is_active_add=1)

    generate_manager_intent_rollups("2026-06-26", windows=(5,))
    row = get_rollup()

    assert row["net_active_score"] == 4.0
    assert row["buy_issuer_count"] == 2
    assert row["intent_direction"] == "accumulation"
    assert row["primary_intent_state"] == "accumulation"
    assert json.loads(row["intent_pattern_tags_json"]) == ["broad_manager_accumulation"]


def test_distribution_classified_when_net_negative_and_breadth_exists():
    setup_db()
    insert_eligible_history(etfs=("00980A", "00982A"))
    insert_change("2026-06-25", "00980A", is_active_reduce=1)
    insert_change("2026-06-26", "00982A", is_active_reduce=1)

    generate_manager_intent_rollups("2026-06-26", windows=(5,))
    row = get_rollup()

    assert row["net_active_score"] == -4.0
    assert row["sell_issuer_count"] == 2
    assert row["intent_direction"] == "distribution"
    assert row["primary_intent_state"] == "distribution"
    assert json.loads(row["intent_pattern_tags_json"]) == ["broad_manager_distribution"]


def test_contested_classified_before_direction_when_issuer_breadth_offsets():
    setup_db()
    insert_eligible_history(etfs=("00980A", "00982A", "00984A", "00985A"))
    insert_change("2026-06-25", "00980A", is_active_add=1)
    insert_change("2026-06-25", "00982A", is_active_add=1)
    insert_change("2026-06-26", "00984A", is_active_reduce=1)
    insert_change("2026-06-26", "00985A", is_active_reduce=1)

    generate_manager_intent_rollups("2026-06-26", windows=(5,))
    row = get_rollup()

    assert row["buy_issuer_count"] == 2
    assert row["sell_issuer_count"] == 2
    assert row["gross_active_score"] == 8.0
    assert row["net_to_gross"] == 0.0
    assert row["intent_direction"] == "contested"
    assert row["primary_intent_state"] == "contested"


def test_active_flag_with_missing_active_delta_fields_falls_back_to_base_score():
    setup_db()
    insert_eligible_history(etfs=("00980A",))
    insert_change("2026-06-26", "00980A", is_active_add=1, active_delta=None, active_delta_pct=None)

    generate_manager_intent_rollups("2026-06-26", windows=(5,))
    row = get_rollup(entity_level="issuer_stock", issuer_key="野村")

    assert row["cum_active_buy_score"] == 2.0
    assert row["net_active_score"] == 2.0


def test_rebuilt_rows_populate_one_built_at_timestamp_for_the_transaction():
    setup_db()
    insert_eligible_history(etfs=("00980A", "00982A"))
    insert_change("2026-06-25", "00980A", is_active_add=1)
    insert_change("2026-06-26", "00982A", is_active_add=1)

    generate_manager_intent_rollups("2026-06-26", windows=(5, 10))

    with db._connect() as conn:
        rows = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT built_at), MIN(built_at) FROM manager_intent_rollups WHERE date = '2026-06-26'"
        ).fetchone()

    assert rows[0] > 0
    assert rows[1] == 1
    assert rows[2]


def test_retired_etf_events_are_included_through_latest_holdings_date():
    setup_db()
    insert_eligible_history(etfs=("00998A",))
    insert_change("2026-06-26", "00998A", is_active_reduce=1)

    generate_manager_intent_rollups("2026-06-26", windows=(5,))
    stock_row = get_rollup()

    assert stock_row["cum_active_sell_score"] == 2.0
    assert stock_row["sell_etf_count"] == 1


def test_retired_etf_events_after_latest_holdings_date_are_excluded_before_scoring():
    setup_db()
    insert_eligible_history(etfs=("00980A",))
    insert_eligible_history(
        etfs=("00999A",),
        dates=["2026-06-20", "2026-06-21"],
    )
    insert_change("2026-06-26", "00980A", is_active_add=1)
    insert_change("2026-06-26", "00999A", is_active_reduce=1)

    generate_manager_intent_rollups("2026-06-26", windows=(5,))
    stock_row = get_rollup()

    assert stock_row["cum_active_buy_score"] == 2.0
    assert stock_row["cum_active_sell_score"] == 0.0
    assert stock_row["sell_etf_count"] == 0


def test_consecutive_active_add_score_replaces_base_score_instead_of_adding_bonus_per_row():
    setup_db()
    insert_eligible_history(etfs=("00980A",))
    for date in ("2026-06-24", "2026-06-25", "2026-06-26"):
        insert_change(date, "00980A", is_active_add=1, consecutive_active_add_days=3)

    generate_manager_intent_rollups("2026-06-26", windows=(5,))
    row = get_rollup(entity_level="issuer_stock", issuer_key="野村")

    assert row["cum_active_buy_score"] == 4.5
    assert row["gross_active_score"] == 4.5


def test_unsupported_window_raises_clear_error():
    setup_db()
    insert_eligible_history(etfs=("00980A",))

    with pytest.raises(ValueError, match="Unsupported manager intent window"):
        generate_manager_intent_rollups("2026-06-26", windows=(7,))


def test_insufficient_eligible_days_classifies_as_insufficient_data():
    setup_db()
    insert_eligible_history(etfs=("00980A",), dates=["2026-06-25", "2026-06-26"])
    insert_change("2026-06-26", "00980A", is_active_add=1)

    generate_manager_intent_rollups("2026-06-26", windows=(5,))
    row = get_rollup(entity_level="issuer_stock", issuer_key="野村")

    assert row["eligible_days"] == 2
    assert row["intent_direction"] == "neutral"
    assert row["primary_intent_state"] == "insufficient_data"
    assert json.loads(row["intent_pattern_tags_json"]) == ["insufficient_data"]


def test_zero_events_for_eligible_stock_classifies_as_neutral():
    setup_db()
    insert_eligible_history(etfs=("00980A",))

    generate_manager_intent_rollups("2026-06-26", windows=(5,))
    row = get_rollup(entity_level="issuer_stock", issuer_key="野村")

    assert row["eligible_days"] == 5
    assert row["gross_active_score"] == 0.0
    assert row["intent_direction"] == "neutral"
    assert row["primary_intent_state"] == "neutral"
    assert json.loads(row["intent_pattern_tags_json"]) == []


def test_high_activity_unclear_when_same_issuer_offsets_without_rotation_classification():
    setup_db()
    insert_eligible_history(etfs=("00980A", "00981A"))
    insert_change("2026-06-25", "00980A", is_active_add=1)
    insert_change("2026-06-25", "00981A", is_active_add=1)
    insert_change("2026-06-26", "00980A", is_active_reduce=1)
    insert_change("2026-06-26", "00981A", is_active_reduce=1)

    generate_manager_intent_rollups("2026-06-26", windows=(5,))
    row = get_rollup(entity_level="issuer_stock", issuer_key="野村")

    assert row["gross_active_score"] == 8.0
    assert row["net_to_gross"] == 0.0
    assert row["intent_direction"] == "unclear"
    assert row["primary_intent_state"] == "high_activity_unclear"
    assert json.loads(row["intent_pattern_tags_json"]) == ["high_gross_low_net"]


def test_cross_fund_rotation_metrics_and_classification_for_balanced_same_issuer_offset():
    setup_db()
    insert_eligible_history(etfs=("00980A", "00981A"))
    insert_change("2026-06-26", "00980A", is_active_add=1)
    insert_change("2026-06-26", "00981A", is_active_reduce=1)

    generate_manager_intent_rollups("2026-06-26", windows=(5,))
    row = get_rollup(entity_level="issuer_stock", issuer_key="野村")

    assert row["rotation_buy_etf_count"] == 1
    assert row["rotation_sell_etf_count"] == 1
    assert row["cross_fund_offset_ratio"] == 1.0
    assert row["intent_direction"] == "rotation"
    assert row["primary_intent_state"] == "cross_fund_rotation"
    assert json.loads(row["intent_pattern_tags_json"]) == ["cross_fund_rotation"]


def test_cross_fund_rotation_with_positive_net_keeps_rotation_accumulation_state():
    setup_db()
    insert_eligible_history(etfs=("00980A", "00981A"))
    insert_change("2026-06-25", "00980A", is_active_add=1)
    insert_change("2026-06-26", "00981A", is_active_add=1)
    insert_change("2026-06-26", "00980A", is_active_reduce=1)

    generate_manager_intent_rollups("2026-06-26", windows=(5,))
    row = get_rollup(entity_level="issuer_stock", issuer_key="野村")

    assert row["net_active_score"] == 2.0
    assert row["cross_fund_offset_ratio"] == 0.5
    assert row["intent_direction"] == "rotation_accumulation"
    assert row["primary_intent_state"] == "cross_fund_rotation_accumulation"
    assert json.loads(row["intent_pattern_tags_json"]) == ["cross_fund_rotation", "rotation_net_accumulation"]
