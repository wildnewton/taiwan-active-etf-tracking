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


def test_get_latest_signal_date_returns_most_recent_date():
    db.init_db(":memory:")
    ensure_signal_table()
    insert_signal(date="2026-06-21", stock_code="2330")
    insert_signal(date="2026-06-23", stock_code="2383")

    assert get_latest_signal_date() == "2026-06-23"


def test_generate_signal_report_groups_signals_by_section():
    db.init_db(":memory:")
    ensure_signal_table()
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
        signal_type="new_core_position",
        signal_strength="strong",
        signal_score=4,
        stock_code="6669",
        stock_name="緯穎",
        etf_codes=["00992A"],
        issuers=["Capital"],
    )
    insert_signal(
        signal_type="consensus_reduce_3d",
        signal_strength="medium",
        signal_score=-4,
        stock_code="2454",
        stock_name="聯發科",
        etf_codes=["00980A", "00981A"],
        issuers=["Nomura", "Uni-President"],
        action_label="Reduce Watch",
    )

    report = generate_signal_report("2026-06-23")

    assert "Taiwan Active ETF Manager Signals" in report
    assert "Latest signal date: 2026-06-23" in report
    assert "Signals generated: 3" in report
    assert "A. Strong consensus adds" in report
    assert "2383 台光電" in report
    assert "Nomura, Uni-President, Fubon" in report
    assert "B. New core positions" in report
    assert "6669 緯穎" in report
    assert "E. Consensus reductions" in report
    assert "2454 聯發科" in report
    assert "Reduce Watch" in report


def test_generate_signal_report_uses_latest_date_when_date_omitted():
    db.init_db(":memory:")
    ensure_signal_table()
    insert_signal(date="2026-06-21", stock_code="2330", stock_name="台積電")
    insert_signal(date="2026-06-23", stock_code="2383", stock_name="台光電")

    report = generate_signal_report()

    assert "Latest signal date: 2026-06-23" in report
    assert "2383 台光電" in report
    assert "2330 台積電" not in report


def test_generate_signal_report_handles_no_signal_table_or_rows():
    db.init_db(":memory:")

    report = generate_signal_report("2026-06-23")

    assert "Taiwan Active ETF Manager Signals" in report
    assert "No manager signals found" in report
