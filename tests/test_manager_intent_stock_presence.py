import pytest

import db
from manager_intent import build_manager_intent_rows

WINDOW_DATES = ["2026-06-22", "2026-06-23", "2026-06-24", "2026-06-25", "2026-06-26"]
ETF_ISSUERS = {"00980A": "野村", "00982A": "統一"}


@pytest.fixture(autouse=True)
def restore_default_db_after_test():
    yield
    if db._MEMORY_CONN is not None:
        db._MEMORY_CONN.close()
        db._MEMORY_CONN = None
    db._DB_PATH = db.DEFAULT_DB_PATH


def _setup_db():
    db.init_db(":memory:")
    with db._connect() as conn:
        for etf_code, issuer in ETF_ISSUERS.items():
            conn.execute(
                """
                INSERT INTO etf_universe (
                    code, name, issuer, market, retired, listing_date,
                    first_seen_date, created_at, updated_at
                ) VALUES (?, ?, ?, 'TWSE', 0, '2026-01-01',
                          '2026-06-01', '2026-06-01T00:00:00',
                          '2026-06-01T00:00:00')
                """,
                (etf_code, f"Test {etf_code}", issuer),
            )


def _insert_snapshot(date, etf_code, stock_code, stock_name):
    stocks = [(stock_code, stock_name)] + [
        (f"230{index}", f"Filler{index}")
        for index in range(1, 5)
    ]
    with db._connect() as conn:
        for code, name in stocks:
            conn.execute(
                """
                INSERT INTO etf_daily_holdings (
                    date, etf_code, asset_name, asset_type, stock_code, stock_name,
                    shares, weight_pct, source_url, source_type, extraction_method,
                    scraped_at
                ) VALUES (?, ?, ?, 'stock', ?, ?, 1000, 5.0, 'https://test',
                          'moneydj_primary', 'test', ?)
                """,
                (
                    date,
                    etf_code,
                    f"{name}({code}.TW)",
                    code,
                    name,
                    f"{date}T00:00:00",
                ),
            )
        conn.execute(
            """
            INSERT INTO etf_daily_non_stock_assets (
                date, etf_code, asset_name, asset_type, weight_pct,
                source_url, source_type, extraction_method, scraped_at
            ) VALUES (?, ?, 'Cash', 'cash', 75.0, 'https://test',
                      'moneydj_primary', 'test', ?)
            """,
            (date, etf_code, f"{date}T00:00:00"),
        )
        conn.execute(
            """
            INSERT INTO etf_change_diagnostics (
                date, prev_date, etf_code, status, reason,
                current_source_type, previous_source_type,
                current_stock_count, previous_stock_count,
                overlap_ratio, size_ratio, created_at
            ) VALUES (?, '2026-06-21', ?, 'included',
                      'comparable_source_pair', 'moneydj_primary',
                      'moneydj_primary', 5, 5, 1.0, 1.0, ?)
            """,
            (date, etf_code, f"{date}T00:00:00"),
        )


def _insert_position_change(date, etf_code, stock_code, stock_name, *, removed=False):
    issuer = ETF_ISSUERS[etf_code]
    score_direction = -1 if removed else 1
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_holding_changes (
                date, etf_code, issuer, stock_code, stock_name, prev_date,
                prev_weight_pct, weight_pct, weight_delta_1d,
                prev_shares, shares, shares_delta_1d,
                active_shares_delta_1d, active_shares_delta_pct_1d,
                prev_rank, rank, is_new_position, is_removed_position,
                position_change_type, active_direction,
                is_active_add, is_active_reduce,
                consecutive_active_add_days, consecutive_active_reduce_days,
                confidence
            ) VALUES (?, ?, ?, ?, ?, '2026-06-25',
                      5.0, 5.0, 0.0, 1000, 1000, 0,
                      ?, ?, 10, 10, ?, ?, ?, ?, 0, 0, 0, 0, 'high')
            """,
            (
                date,
                etf_code,
                issuer,
                stock_code,
                stock_name,
                100.0 * score_direction,
                10.0 * score_direction,
                0 if removed else 1,
                1 if removed else 0,
                "removed_position" if removed else "new_position",
                "reduce" if removed else "add",
            ),
        )


def _row(rows, *, entity_level="stock", issuer_key="", stock_code="2330"):
    return next(
        row
        for row in rows
        if row["entity_level"] == entity_level
        and row["issuer_key"] == issuer_key
        and row["stock_code"] == stock_code
    )


def test_new_position_history_counts_only_dates_stock_is_in_canonical_holdings():
    _setup_db()
    for date in WINDOW_DATES[:-1]:
        for etf_code in ETF_ISSUERS:
            _insert_snapshot(date, etf_code, "2317", "鴻海")
    for etf_code in ETF_ISSUERS:
        _insert_snapshot(WINDOW_DATES[-1], etf_code, "2330", "台積電")
        _insert_position_change(WINDOW_DATES[-1], etf_code, "2330", "台積電")

    row = _row(build_manager_intent_rows(WINDOW_DATES[-1], 5))

    assert row["eligible_days"] == 1
    assert row["primary_intent_state"] == "insufficient_data"


def test_removed_position_keeps_only_prior_holding_dates_as_history():
    _setup_db()
    for date in WINDOW_DATES[:-1]:
        _insert_snapshot(date, "00980A", "2330", "台積電")
    _insert_snapshot(WINDOW_DATES[-1], "00980A", "2317", "鴻海")
    _insert_position_change(
        WINDOW_DATES[-1],
        "00980A",
        "2330",
        "台積電",
        removed=True,
    )

    row = _row(
        build_manager_intent_rows(WINDOW_DATES[-1], 5),
        entity_level="issuer_stock",
        issuer_key="野村",
    )

    assert row["eligible_days"] == 4
    assert row["cum_active_sell_score"] == 4.0
