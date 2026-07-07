import json
import sqlite3

import db
from report import generate_signal_report, get_latest_signal_date



def ensure_signal_table():
    with db._connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS etf_manager_signals (
                date TEXT NOT NULL,
                signal_id TEXT PRIMARY KEY,
                signal_type TEXT NOT NULL,
                signal_strength TEXT NOT NULL,
                signal_score REAL NOT NULL,
                stock_code TEXT NOT NULL,
                stock_name TEXT,
                etf_codes TEXT NOT NULL,
                issuers TEXT NOT NULL,
                etf_count INTEGER NOT NULL,
                issuer_count INTEGER NOT NULL,
                explanation TEXT,
                evidence_json TEXT,
                action_label TEXT,
                confidence TEXT,
                created_at TEXT NOT NULL
            )
            """
        )



def insert_signal(
    date="2026-06-23",
    signal_type="new_core_position",
    signal_strength="medium",
    signal_score=4,
    stock_code="2330",
    stock_name="台積電",
    etf_codes=None,
    issuers=None,
    action_label="Watch",
):
    etf_codes = etf_codes or ["00980A"]
    issuers = issuers or ["Nomura"]
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_manager_signals (
                date, signal_id, signal_type, signal_strength, signal_score,
                stock_code, stock_name, etf_codes, issuers, etf_count,
                issuer_count, explanation, evidence_json, action_label,
                confidence, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'normal',
                '2026-06-23T00:00:00')
            """,
            (
                date,
                f"{date}:{signal_type}:{stock_code}:{'-'.join(etf_codes)}",
                signal_type,
                signal_strength,
                signal_score,
                stock_code,
                stock_name,
                json.dumps(etf_codes, ensure_ascii=False),
                json.dumps(issuers, ensure_ascii=False),
                len(etf_codes),
                len(issuers),
                f"{stock_code} generated {signal_type}",
                json.dumps([{"stock_code": stock_code}], ensure_ascii=False),
                action_label,
            ),
        )



def insert_holding(date, etf_code, stock_code, stock_name, weight_pct=5.0):
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_daily_holdings (
                date, etf_code, asset_name, asset_type, stock_code, stock_name,
                shares, weight_pct, source_url, source_type, extraction_method,
                scraped_at
            ) VALUES (?, ?, ?, 'stock', ?, ?, 1000, ?, 'https://test', 'test', 'test', ?)
            """,
            (date, etf_code, f"{stock_name}({stock_code}.TW)", stock_code, stock_name, weight_pct, f"{date}T00:00:00"),
        )



def test_get_latest_signal_date_returns_most_recent_date():
    db.init_db(":memory:")
    ensure_signal_table()
    insert_signal(date="2026-06-21", stock_code="2330")
    insert_signal(date="2026-06-23", stock_code="2383")

    assert get_latest_signal_date() == "2026-06-23"



def test_generate_signal_report_shows_summary_and_signals():
    """New report format: summary + changes + signals."""
    db.init_db(":memory:")
    ensure_signal_table()

    # Insert holdings so report has data_date
    insert_holding("2026-06-23", "00980A", "2383", "台光電", 5.0)
    insert_holding("2026-06-23", "00981A", "2383", "台光電", 3.0)
    insert_holding("2026-06-23", "00405A", "2383", "台光電", 4.0)
    insert_holding("2026-06-23", "00980A", "2454", "聯發科", 6.0)
    insert_holding("2026-06-23", "00981A", "2454", "聯發科", 5.0)

    insert_signal(
        signal_type="consensus_add_3d",
        signal_strength="strong",
        signal_score=6,
        stock_code="2383",
        stock_name="台光電",
        etf_codes=["00980A", "00981A", "00405A"],
        issuers=["Nomura", "Uni-President", "Fubon"],
    )
    insert_signal(
        signal_type="consensus_reduce_3d",
        signal_strength="medium",
        signal_score=-6,
        stock_code="2454",
        stock_name="聯發科",
        etf_codes=["00980A", "00981A"],
        issuers=["Nomura", "Uni-President"],
        action_label="Reduce Watch",
    )

    report = generate_signal_report("2026-06-23")

    # New format checks
    assert "📊 台灣主動 ETF 每日報告" in report
    assert "═══ 摘要 ═══" in report
    # Signals section should appear when signals exist without pinning presentation details.
    assert "管理人訊號" in report
    assert "2383 台光電" in report
    assert "2454 聯發科" in report



def test_generate_signal_report_uses_latest_holdings_date():
    """Report should use latest holdings date."""
    db.init_db(":memory:")
    ensure_signal_table()

    # Insert holdings for two dates
    insert_holding("2026-06-21", "00980A", "2330", "台積電", 10.0)
    insert_holding("2026-06-23", "00980A", "2383", "台光電", 5.0)
    insert_signal(date="2026-06-21", stock_code="2330", stock_name="台積電")
    insert_signal(date="2026-06-23", stock_code="2383", stock_name="台光電")

    report = generate_signal_report()

    # Report should mention data date from holdings (2026-06-23)
    assert "2026-06-23" in report
    assert "摘要" in report



def test_generate_signal_report_handles_no_signals():
    """Report should still work when no signals exist (e.g., <3 days of data)."""
    db.init_db(":memory:")

    report = generate_signal_report("2026-06-23")

    assert "📊 台灣主動 ETF 每日報告" in report
    # No signals section when there are none
    assert "管理人訊號" not in report



def test_report_warns_when_etfs_missing():
    """⚠️ Report should warn when holdings have < 19 ETFs for latest date."""
    db.init_db(":memory:")
    ensure_signal_table()

    # Insert holdings for only 13 ETFs (simulating incomplete scrape)
    for i in range(400, 413):
        etf = f"00{i}A"
        insert_holding("2026-06-26", etf, str(2300 + i), f"Stock{i}", 5.0)

    report = generate_signal_report("2026-06-26")

    assert "⚠️" in report, f"Expected ⚠️ warning in report:\n{report}"
    assert "13" in report, f"Expected 13 ETFs mentioned:\n{report}"
    assert "19" in report, f"Expected 19 total mentioned:\n{report}"
    assert "缺失" in report or "不完整" in report or "預期" in report



def test_report_no_warning_when_all_etfs_present():
    """No missing-ETF warning when all 19 ETFs have holdings data."""
    db.init_db(":memory:")
    ensure_signal_table()

    # Insert holdings for all 19 ETFs with realistic weights
    codes = ["00400A", "00401A", "00403A", "00404A", "00405A",
             "00406A", "00980A", "00981A", "00982A", "00984A",
             "00985A", "00987A", "00991A", "00992A", "00993A",
             "00994A", "00995A", "00996A", "00999A"]
    for i, etf in enumerate(codes):
        insert_holding("2026-06-26", etf, str(2300 + i), f"Stock{i}", 85.0)

    report = generate_signal_report("2026-06-26")

    # Should NOT have the missing-ETF warning
    assert "預期" not in report, f"Unexpected missing-ETF warning:\n{report}"
