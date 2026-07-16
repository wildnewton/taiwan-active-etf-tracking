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
    for idx in range(19):
        _insert_etf_universe_entry(f"ETF{idx:02d}", f"ETF {idx}", "Issuer", 0)
    for date_value, count in (
        ("2026-06-20", 15),
        ("2026-06-21", 16),
        ("2026-06-22", 16),
    ):
        for idx in range(count):
            insert_holding(
                date_value,
                f"ETF{idx:02d}",
                f"{idx:04d}",
                f"Stock {idx}",
                100,
                5.0,
            )

    assert get_latest_valid_date() == "2026-06-22"
    assert get_previous_valid_date("2026-06-22") == "2026-06-21"



def test_detects_new_removed_and_existing_position_changes():
    db.init_db(":memory:")

    _insert_etf_universe_entry("00980A", "Test ETF", "TestIssuer", 0)

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
    assert summary["previous_date"] == "2026-06-20"
    assert summary["previous_date_weekday"] == "週六"

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

    _insert_etf_universe_entry("00980A", "Test ETF", "TestIssuer", 0)

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

    _insert_etf_universe_entry("00980A", "Test ETF", "TestIssuer", 0)

    insert_holding("2026-06-20", "00980A", "2330", "台積電", 120, 4.0)
    insert_holding("2026-06-21", "00980A", "2330", "台積電", 110, 2.0)
    insert_holding("2026-06-22", "00980A", "2330", "台積電", 100, 1.0)

    detect_holding_changes("2026-06-22", "2026-06-21")

    row = fetch_change("2330", date="2026-06-22")
    assert row["weight_delta_1d"] == -1.0
    assert row["weight_delta_3d"] == -3.0
    assert row["consecutive_add_days"] == 0
    assert row["consecutive_reduce_days"] == 2


def _insert_etf_universe_entry(code, name, issuer, retired):
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_universe
                (code, name, issuer, market, retired,
                 first_seen_date, last_active_date, created_at, updated_at)
            VALUES (?, ?, ?, 'TWSE', ?,
                    '2026-06-20', '2026-06-23', datetime('now'), datetime('now'))
            """,
            (code, name, issuer, retired),
        )


def _insert_scrape_success(date_value, etf_code):
    with db._connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO etf_scrape_runs (
                date, data_date, etf_code, status, primary_source, primary_success,
                moneydj_browser_used, official_fallback_used, official_success,
                rows_extracted, stock_rows_extracted, non_stock_rows_extracted,
                total_weight_all_rows, total_weight_stock_rows, source_url,
                error, started_at, finished_at
            ) VALUES (?, ?, ?, 'success', 'moneydj_primary', 1, 0, 0, 0,
                10, 8, 2, 100.0, 95.0, 'https://example.test', NULL,
                '2026-06-23T00:00:00', '2026-06-23T00:01:00')
            """,
            (date_value, date_value, etf_code),
        )


def test_excludes_retired_etfs_from_change_detection():
    """Retired (retired=1) ETFs must not appear in etf_holding_changes.

    The data pipeline should treat retired ETFs as out-of-scope for all
    downstream analysis: diagnostics, change detection, and signals.
    """
    db.init_db(":memory:")

    _insert_etf_universe_entry("ACTIVE", "Active ETF", "ActiveAM", 0)
    _insert_etf_universe_entry("RETIRED", "Retired ETF", None, 1)

    # Both have holdings on both dates
    insert_holding("2026-06-20", "ACTIVE", "2330", "TSMC", 100, 10.0)
    insert_holding("2026-06-20", "RETIRED", "2498", "HTC", 50, 5.0)

    insert_holding("2026-06-23", "ACTIVE", "2330", "TSMC", 110, 12.0)
    insert_holding("2026-06-23", "RETIRED", "2498", "HTC", 55, 5.5)

    # Active ETF needs scrape success to pass 80% threshold
    _insert_scrape_success("2026-06-20", "ACTIVE")
    _insert_scrape_success("2026-06-23", "ACTIVE")

    summary = detect_holding_changes("2026-06-23", "2026-06-20")

    assert summary["ok"] is True
    assert summary["rows"] > 0

    # Active ETF change MUST exist
    active_row = fetch_change("2330", etf_code="ACTIVE", date="2026-06-23")
    assert active_row is not None, "Active ETF should produce change rows"
    assert active_row["issuer"] == "ActiveAM"
    assert active_row["weight_delta_1d"] == 2.0

    # Retired ETF MUST NOT appear
    retired_row = fetch_change("2498", etf_code="RETIRED", date="2026-06-23")
    assert retired_row is None, (
        "Retired ETF must not appear in change detection; "
        f"found row with issuer={retired_row['issuer'] if retired_row else 'None'}"
    )


def test_retired_etf_with_null_issuer_does_not_crash():
    """A retired ETF with issuer=NULL must not cause IntegrityError.

    Regression test: the pipeline crashed with
    'NOT NULL constraint failed: etf_holding_changes.issuer' because
    retired ETFs with NULL issuer were still processed by
    _source_pair_diagnostics → _build_change_row → _persist_changes.
    """
    db.init_db(":memory:")

    _insert_etf_universe_entry("ACTIVE", "Active ETF", "ActiveAM", 0)
    _insert_etf_universe_entry("RETIRED", "Retired NULL", None, 1)

    insert_holding("2026-06-20", "ACTIVE", "2330", "TSMC", 100, 10.0)
    insert_holding("2026-06-20", "RETIRED", "2498", "HTC", 50, 5.0)
    insert_holding("2026-06-23", "ACTIVE", "2330", "TSMC", 110, 12.0)
    insert_holding("2026-06-23", "RETIRED", "2498", "HTC", 55, 5.5)

    _insert_scrape_success("2026-06-20", "ACTIVE")
    _insert_scrape_success("2026-06-23", "ACTIVE")

    summary = detect_holding_changes("2026-06-23", "2026-06-20")

    assert summary["ok"] is True
    retired_row = fetch_change("2498", etf_code="RETIRED", date="2026-06-23")
    assert retired_row is None
