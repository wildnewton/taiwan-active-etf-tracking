import db
from report import generate_signal_report


ETF_CODES = [
    "00400A", "00401A", "00403A", "00404A", "00405A",
    "00406A", "00980A", "00981A", "00982A", "00984A",
    "00985A", "00987A", "00991A", "00992A", "00993A",
    "00994A", "00995A", "00996A", "00999A",
]


def insert_holding(
    date,
    etf_code,
    stock_code="2330",
    stock_name="台積電",
    weight_pct=90.0,
    source_type="moneydj_primary",
    shares=1000,
):
    from etf_universe import upsert_etf

    upsert_etf({"code": etf_code, "name": f"ETF {etf_code}", "market": "TWSE"})
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_daily_holdings (
                date, etf_code, asset_name, asset_type, stock_code, stock_name,
                shares, weight_pct, source_url, source_type, extraction_method,
                scraped_at
            ) VALUES (?, ?, ?, 'stock', ?, ?, ?, ?, 'https://test', ?, 'test', ?)
            """,
            (
                date,
                etf_code,
                f"{stock_name}({stock_code}.TW)",
                stock_code,
                stock_name,
                shares,
                weight_pct,
                source_type,
                f"{date}T00:00:00",
            ),
        )
        stock_total = conn.execute(
            """
            SELECT COALESCE(SUM(weight_pct), 0.0)
            FROM etf_daily_holdings
            WHERE date = ? AND etf_code = ? AND source_type = ?
            """,
            (date, etf_code, source_type),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT OR REPLACE INTO etf_daily_non_stock_assets (
                date, etf_code, asset_name, asset_type, weight_pct,
                source_url, source_type, extraction_method, scraped_at
            ) VALUES (?, ?, '現金', 'cash', ?, 'https://test', ?, 'test', ?)
            """,
            (
                date,
                etf_code,
                100.0 - stock_total,
                source_type,
                f"{date}T00:00:00",
            ),
        )


def insert_full_day(date, canonical_weight=90.0, duplicate_weight=1.0):
    for etf_code in ETF_CODES:
        insert_holding(date, etf_code, "2330", "台積電", canonical_weight, "moneydj_primary")
        insert_holding(date, etf_code, "2454", "聯發科", duplicate_weight, "official_static")


def test_report_summary_stats_ignore_noncanonical_holdings_rows():
    db.init_db(":memory:")
    insert_full_day("2026-06-26")

    report = generate_signal_report("2026-06-26")

    assert "ETF 數量: 19 | 股票檔數: 1 | 非股票資產: 19" in report


def test_report_consensus_ignores_noncanonical_duplicate_source_rows():
    db.init_db(":memory:")
    insert_full_day("2026-06-26")

    report = generate_signal_report("2026-06-26")

    assert "2330 台積電" in report
    assert "2454 聯發科" not in report


def test_report_consensus_weight_delta_uses_canonical_rows_for_both_dates():
    db.init_db(":memory:")
    for etf_code in ETF_CODES:
        insert_holding("2026-06-25", etf_code, "2330", "台積電", 10.0, "moneydj_primary")
        insert_holding("2026-06-25", etf_code, "2454", "聯發科", 99.0, "official_static")
        insert_holding("2026-06-26", etf_code, "2330", "台積電", 11.0, "moneydj_primary")
        insert_holding("2026-06-26", etf_code, "2454", "聯發科", 99.0, "official_static")

    report = generate_signal_report("2026-06-26")

    assert "2330 台積電" in report
    assert "總權重Δ+19.00%" in report
    assert "總權重Δ+1900.00%" not in report


def test_report_data_warnings_use_canonical_source_totals():
    db.init_db(":memory:")
    for etf_code in ETF_CODES:
        canonical_weight = 50.0 if etf_code == "00400A" else 90.0
        insert_holding("2026-06-26", etf_code, "2330", "台積電", canonical_weight, "moneydj_primary")
        insert_holding("2026-06-26", etf_code, "2454", "聯發科", 50.0, "official_static")

    report = generate_signal_report("2026-06-26")

    assert "00400A: 股票權重僅 50.0%" in report
