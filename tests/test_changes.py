import sqlite3

import db
from changes import detect_holding_changes, get_latest_valid_date, get_previous_valid_date


def insert_holding(date, etf_code, stock_code, stock_name, shares, weight_pct):
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_daily_holdings (
                date, etf_code, asset_name, asset_type, stock_code, stock_name,
                shares, weight_pct, source_url, source_type, extraction_method,
                scraped_at
            ) VALUES (?, ?, ?, 'stock', ?, ?, ?, ?, 'https://example.test',
                'moneydj_primary', 'test', '2026-06-23T00:00:00')
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


def fetch_change(stock_code, etf_code="00980A", date="2026-06-23"):
    with db._connect() as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            """
            SELECT *
            FROM etf_holding_changes
            WHERE date = ? AND etf_code = ? AND stock_code = ?
            """,
            (date, etf_code, stock_code),
        ).fetchone()


def insert_success_runs(date_value, count=16):
    with db._connect() as conn:
        for idx in range(count):
            conn.execute(
                """
                INSERT OR REPLACE INTO etf_scrape_runs (
                    date, etf_code, status, primary_source, primary_success,
                    moneydj_browser_used, official_fallback_used, official_success,
                    rows_extracted, stock_rows_extracted, non_stock_rows_extracted,
                    total_weight_all_rows, total_weight_stock_rows, source_url,
                    error, started_at, finished_at
                ) VALUES (?, ?, 'success', 'moneydj_primary', 1, 0, 0, 0,
                    10, 8, 2, 100.0, 95.0, 'https://example.test', NULL,
                    '2026-06-23T00:00:00', '2026-06-23T00:01:00')
                """,
                (date_value, f"ETF{idx:02d}"),
            )


def test_init_db_creates_change_detection_table():
    db.init_db(":memory:")

    with db._connect() as conn:
        row = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name = 'etf_holding_changes'
            """
        ).fetchone()

    assert row[0] == "etf_holding_changes"


def test_get_latest_and_previous_valid_dates_use_80_percent_success_threshold():
    db.init_db(":memory:")
    insert_success_runs("2026-06-20", count=15)
    insert_success_runs("2026-06-21", count=16)
    insert_success_runs("2026-06-22", count=16)

    assert get_latest_valid_date() == "2026-06-22"
    assert get_previous_valid_date("2026-06-22") == "2026-06-21"


def test_detects_new_removed_and_existing_position_changes():
    db.init_db(":memory:")

    insert_holding("2026-06-20", "00980A", "2330", "台積電", 100, 10.0)
    insert_holding("2026-06-20", "00980A", "2308", "台達電", 200, 5.0)
    insert_holding("2026-06-20", "00980A", "2383", "台光電", 300, 3.0)

    insert_holding("2026-06-23", "00980A", "2330", "台積電", 110, 12.0)
    insert_holding("2026-06-23", "00980A", "6669", "緯穎", 50, 6.0)
    insert_holding("2026-06-23", "00980A", "2308", "台達電", 190, 4.0)

    summary = detect_holding_changes("2026-06-23", "2026-06-20")

    assert summary["ok"] is True
    assert summary["rows"] == 4
    assert summary["new_positions"] == 1
    assert summary["removed_positions"] == 1

    tsmc = fetch_change("2330")
    assert tsmc["prev_weight_pct"] == 10.0
    assert tsmc["weight_pct"] == 12.0
    assert tsmc["weight_delta_1d"] == 2.0
    assert tsmc["shares_delta_1d"] == 10.0
    assert tsmc["rank_delta_1d"] == 0
    assert tsmc["is_new_position"] == 0
    assert tsmc["is_removed_position"] == 0

    win = fetch_change("6669")
    assert win["is_new_position"] == 1
    assert win["prev_weight_pct"] is None
    assert win["weight_pct"] == 6.0
    assert win["weight_delta_1d"] == 6.0
    assert win["prev_rank"] is None
    assert win["rank"] == 2

    elite = fetch_change("2383")
    assert elite["is_removed_position"] == 1
    assert elite["prev_weight_pct"] == 3.0
    assert elite["weight_pct"] == 0.0
    assert elite["weight_delta_1d"] == -3.0
    assert elite["prev_rank"] == 3
    assert elite["rank"] is None

    delta = fetch_change("2308")
    assert delta["weight_delta_1d"] == -1.0
    assert delta["shares_delta_1d"] == -10.0
    assert delta["rank_delta_1d"] == -1


def test_detects_three_day_rolling_delta_and_consecutive_adds():
    db.init_db(":memory:")

    insert_holding("2026-06-20", "00980A", "2330", "台積電", 100, 1.0)
    insert_holding("2026-06-21", "00980A", "2330", "台積電", 110, 2.0)
    insert_holding("2026-06-22", "00980A", "2330", "台積電", 120, 4.0)

    summary = detect_holding_changes("2026-06-22", "2026-06-21")

    assert summary["ok"] is True
    row = fetch_change("2330", date="2026-06-22")
    assert row["weight_delta_1d"] == 2.0
    assert row["weight_delta_3d"] == 3.0
    assert row["weight_delta_5d"] is None
    assert row["consecutive_add_days"] == 2
    assert row["consecutive_reduce_days"] == 0


def test_detects_consecutive_reductions():
    db.init_db(":memory:")

    insert_holding("2026-06-20", "00980A", "2330", "台積電", 120, 4.0)
    insert_holding("2026-06-21", "00980A", "2330", "台積電", 110, 2.0)
    insert_holding("2026-06-22", "00980A", "2330", "台積電", 100, 1.0)

    detect_holding_changes("2026-06-22", "2026-06-21")

    row = fetch_change("2330", date="2026-06-22")
    assert row["weight_delta_1d"] == -1.0
    assert row["weight_delta_3d"] == -3.0
    assert row["consecutive_add_days"] == 0
    assert row["consecutive_reduce_days"] == 2
