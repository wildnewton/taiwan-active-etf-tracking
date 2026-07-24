import db
from report import generate_signal_report


ETF_CODES = ["00980A", "00981A"]


def insert_holding(date, etf_code, weight_pct=100.0):
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_daily_holdings (
                date, etf_code, asset_name, asset_type, stock_code, stock_name,
                shares, weight_pct, source_url, source_type, extraction_method,
                scraped_at
            ) VALUES (?, ?, '台積電(2330.TW)', 'stock', '2330', '台積電',
                1000, ?, 'https://test', 'moneydj_primary', 'test', ?)
            """,
            (date, etf_code, weight_pct, f"{date}T00:00:00"),
        )


def insert_full_holdings_day(date):
    from etf_universe import upsert_etf

    for etf_code in ETF_CODES:
        upsert_etf({"code": etf_code, "name": f"ETF {etf_code}", "market": "TWSE"})
        insert_holding(date, etf_code)


def insert_change_diagnostic(
    date,
    prev_date,
    etf_code,
    status,
    reason,
    current_source_type="moneydj_primary",
    previous_source_type="moneydj_primary",
    created_at=None,
):
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_change_diagnostics (
                date, prev_date, etf_code, status, reason,
                current_source_type, previous_source_type,
                current_stock_count, previous_stock_count,
                overlap_ratio, size_ratio, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 10, 10, 1.0, 1.0, ?)
            """,
            (
                date,
                prev_date,
                etf_code,
                status,
                reason,
                current_source_type,
                previous_source_type,
                created_at or f"{date}T00:00:00",
            ),
        )


def retire_test_etf(etf_code):
    from etf_universe import retire_etf

    retire_etf(etf_code, reason="test retired")


def test_report_shows_skipped_change_diagnostics_in_data_quality():
    db.init_db(":memory:")
    insert_full_holdings_day("2026-06-25")
    insert_full_holdings_day("2026-06-26")
    insert_change_diagnostic(
        "2026-06-26",
        "2026-06-25",
        "00980A",
        "skipped",
        "incompatible_source_pair",
        current_source_type="official_static",
        previous_source_type="moneydj_primary",
    )
    insert_change_diagnostic(
        "2026-06-26",
        "2026-06-25",
        "00981A",
        "skipped",
        "missing_previous_source",
        current_source_type="moneydj_primary",
        previous_source_type=None,
    )

    report = generate_signal_report("2026-06-26")

    assert "變更偵測跳過" in report
    assert "00980A incompatible_source_pair" in report
    assert "official_static→moneydj_primary" in report
    assert "00981A missing_previous_source" in report


def test_skipped_change_diagnostics_degrade_report_trust():
    db.init_db(":memory:")
    insert_full_holdings_day("2026-06-25")
    insert_full_holdings_day("2026-06-26")
    insert_change_diagnostic(
        "2026-06-26", "2026-06-25", "00980A", "skipped", "missing_current_source"
    )

    report = generate_signal_report("2026-06-26")

    assert "資料品質: ⚠️ Degraded" in report


def test_report_ignores_included_change_diagnostics():
    db.init_db(":memory:")
    insert_full_holdings_day("2026-06-25")
    insert_full_holdings_day("2026-06-26")
    insert_change_diagnostic(
        "2026-06-26", "2026-06-25", "00980A", "included", "comparable_source_pair"
    )

    report = generate_signal_report("2026-06-26")

    assert "資料品質: ✅ Clean" in report
    assert "變更偵測跳過" not in report


def test_report_handles_missing_change_diagnostics_table():
    db.init_db(":memory:")
    insert_full_holdings_day("2026-06-26")
    with db._connect() as conn:
        conn.execute("DROP TABLE etf_change_diagnostics")

    report = generate_signal_report("2026-06-26")

    assert "台灣主動 ETF 每日報告" in report
    assert "變更偵測跳過" not in report


def test_report_uses_latest_diagnostics_run_when_previous_holding_date_differs():
    db.init_db(":memory:")
    insert_full_holdings_day("2026-06-24")
    insert_full_holdings_day("2026-06-25")
    insert_full_holdings_day("2026-06-26")
    insert_change_diagnostic(
        "2026-06-26",
        "2026-06-24",
        "00980A",
        "skipped",
        "incompatible_source_pair",
        created_at="2026-06-26T09:00:00",
    )

    report = generate_signal_report("2026-06-26")

    assert "00980A incompatible_source_pair" in report


def test_retired_etf_after_its_latest_holdings_date_does_not_appear_in_report():
    db.init_db(":memory:")
    insert_full_holdings_day("2026-06-25")
    insert_full_holdings_day("2026-06-26")
    with db._connect() as conn:
        conn.execute(
            "DELETE FROM etf_daily_holdings WHERE date = '2026-06-26' AND etf_code = '00980A'"
        )
    retire_test_etf("00980A")
    insert_change_diagnostic(
        "2026-06-26",
        "2026-06-25",
        "00980A",
        "skipped",
        "retired_diagnostic",
    )

    report = generate_signal_report("2026-06-26")

    assert "變更偵測跳過" not in report
    assert "00980A retired_diagnostic" not in report


def test_active_etf_with_skipped_change_diagnostic_still_appears_in_report():
    db.init_db(":memory:")
    insert_full_holdings_day("2026-06-25")
    insert_full_holdings_day("2026-06-26")
    insert_change_diagnostic(
        "2026-06-26",
        "2026-06-25",
        "00981A",
        "skipped",
        "active_diagnostic",
    )

    report = generate_signal_report("2026-06-26")

    assert "變更偵測跳過" in report
    assert "00981A active_diagnostic" in report
