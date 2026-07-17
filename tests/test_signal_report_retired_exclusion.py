"""Tests for signal report data quality section — retired ETF exclusion."""
import db
from report import generate_signal_report


def _seed_universe(conn, codes_with_status):
    """Insert etf_universe rows. codes_with_status: list of (code, issuer, retired)."""
    conn.execute("DROP TABLE IF EXISTS etf_universe")
    conn.execute("""
        CREATE TABLE etf_universe (
            code TEXT PRIMARY KEY, name TEXT NOT NULL, issuer TEXT,
            market TEXT, isin TEXT, listing_date TEXT,
            retired INTEGER NOT NULL DEFAULT 0,
            first_seen_date TEXT, last_active_date TEXT,
            pending_retirement_since TEXT,
            official_url TEXT, official_method TEXT, official_logic TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )
    """)
    for code, issuer, retired in codes_with_status:
        conn.execute(
            "INSERT INTO etf_universe (code, name, issuer, retired, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, '2026-06-22T00:00:00', '2026-06-22T00:00:00')",
            (code, f"Test {code}", issuer, retired),
        )


def _seed_change_diagnostic(conn, date, etf_code, reason, status="skipped"):
    conn.execute("""
        INSERT INTO etf_change_diagnostics
        (date, prev_date, etf_code, status, reason, current_source_type,
         previous_source_type, overlap_ratio, size_ratio, created_at)
        VALUES (?, '2026-06-21', ?, ?, ?, 'moneydj_primary', 'moneydj_primary',
                0.0, 0.0, datetime('now'))
    """, (date, etf_code, status, reason))


def test_missing_target_holdings_excludes_retired_etfs():
    db.init_db(":memory:")
    with db._connect() as conn:
        _seed_universe(conn, [
            ("00983A", "CTBC", 1),
            ("00980A", "Nomura", 0),
        ])

    report_text = generate_signal_report("2026-06-23")
    assert "00980A" in report_text
    assert "00983A" not in report_text


def test_change_skips_exclude_retired_etfs():
    """RED: Retired ETFs should NOT appear in the 變更偵測跳過 section."""
    db.init_db(":memory:")

    with db._connect() as conn:
        _seed_universe(conn, [
            ("00990A", "Yuanta", 1),  # retired — should NOT appear
            ("00404A", "AB", 0),       # active
        ])
        _seed_change_diagnostic(conn, "2026-06-23", "00990A", "missing_current_source")
        _seed_change_diagnostic(conn, "2026-06-23", "00404A", "missing_current_source")

    report = generate_signal_report("2026-06-23")

    assert "00404A" in report, f"Active skipped ETF should appear:\n{report}"
    assert "00990A" not in report, (
        f"Retired ETF 00990A should NOT appear in report:\n{report}"
    )
