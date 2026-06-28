import json

import db
from report import generate_signal_report


ETF_CODES = [
    "00400A", "00401A", "00403A", "00404A", "00405A",
    "00406A", "00980A", "00981A", "00982A", "00984A",
    "00985A", "00987A", "00991A", "00992A", "00993A",
    "00994A", "00995A", "00996A", "00999A",
]


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


def insert_scrape_run(date, etf_code, status="success"):
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_scrape_runs (
                date, etf_code, status, primary_source, primary_success,
                moneydj_browser_used, official_fallback_used, official_success,
                rows_extracted, stock_rows_extracted, non_stock_rows_extracted,
                total_weight_all_rows, total_weight_stock_rows, source_url, error,
                started_at, finished_at
            ) VALUES (?, ?, ?, 'moneydj_primary', ?, 0, 0, 0, 1, 1, 0, 90, 90,
                'https://test', ?, ?, ?)
            """,
            (
                date,
                etf_code,
                status,
                1 if status == "success" else 0,
                None if status == "success" else "test failure",
                f"{date}T00:00:00",
                f"{date}T00:01:00",
            ),
        )


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
                signal_freshness TEXT DEFAULT 'current',
                freshness_reason TEXT,
                created_at TEXT NOT NULL
            )
            """
        )


def insert_signal(
    date="2026-06-26",
    stock_code="2330",
    stock_name="台積電",
    signal_type="consensus_add_3d",
    signal_score=6,
    signal_strength="strong",
    action_label="Watch",
    freshness="new",
    reason="first reaches consensus",
    issuers=None,
    etf_codes=None,
):
    issuers = issuers or ["野村", "統一"]
    etf_codes = etf_codes or ["00980A", "00981A"]
    evidence = [
        {
            "date": date,
            "etf_code": etf_codes[0],
            "issuer": issuers[0],
            "stock_code": stock_code,
            "stock_name": stock_name,
            "active_shares_delta_pct_1d": 2.5,
            "position_change_type": "confirmed_active_add",
        }
    ]
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_manager_signals (
                date, signal_id, signal_type, signal_strength, signal_score,
                stock_code, stock_name, etf_codes, issuers, etf_count,
                issuer_count, explanation, evidence_json, action_label,
                confidence, signal_freshness, freshness_reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'high', ?, ?, ?)
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
                json.dumps(evidence, ensure_ascii=False),
                action_label,
                freshness,
                reason,
                f"{date}T00:00:00",
            ),
        )


def insert_change(date, stock_code, stock_name, *, etf_code="00980A", prev_weight=0.0, weight=0.0, is_removed=0):
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_holding_changes (
                date, etf_code, issuer, stock_code, stock_name, prev_date,
                prev_weight_pct, weight_pct, weight_delta_1d, prev_shares, shares,
                shares_delta_1d, active_shares_delta_1d, active_shares_delta_pct_1d,
                prev_rank, rank, is_new_position, is_removed_position,
                position_change_type, active_direction, is_active_add, is_active_reduce,
                confidence, source_type, created_at
            ) VALUES (?, ?, '測試投信', ?, ?, '2026-06-25', ?, ?, ?, 1000, 0,
                -1000, -1000, -100.0, 10, NULL, 0, ?, 'removed_position',
                'reduce', 0, 1, 'high', 'moneydj_primary', ?)
            """,
            (
                date,
                etf_code,
                stock_code,
                stock_name,
                prev_weight,
                weight,
                weight - prev_weight,
                is_removed,
                f"{date}T00:00:00",
            ),
        )


def test_report_puts_data_quality_before_summary_and_shows_failed_etfs():
    db.init_db(":memory:")
    for etf_code in ETF_CODES[1:]:
        insert_holding("2026-06-26", etf_code)
        insert_scrape_run("2026-06-26", etf_code)
    insert_scrape_run("2026-06-26", "00400A", status="failed")

    report = generate_signal_report("2026-06-26")

    assert "═══ 資料品質 / 信任度 ═══" in report
    assert report.index("資料品質 / 信任度") < report.index("═══ 摘要 ═══")
    assert "資料品質: ⚠️ Degraded" in report
    assert "成功持倉 ETF: 18/19" in report
    assert "00400A" in report


def test_report_groups_manager_signals_by_freshness_before_exposure_movers():
    db.init_db(":memory:")
    ensure_signal_table()
    insert_signal(stock_code="2330", stock_name="台積電", freshness="new", reason="first consensus")
    insert_signal(stock_code="2454", stock_name="聯發科", freshness="stale", reason="no current-day event")

    report = generate_signal_report("2026-06-26")

    assert "═══ 📈 管理人訊號（按新鮮度） ═══" in report
    assert "🔥 Fresh consensus" in report
    assert "🧊 Stale / fading consensus" in report
    assert report.index("2330 台積電") < report.index("2454 聯發科")
    assert report.index("管理人訊號") < report.index("Exposure movers")


def test_report_hides_tiny_removed_positions_by_default():
    db.init_db(":memory:")
    insert_change("2026-06-26", "3363", "上詮", prev_weight=0.89, is_removed=1)
    insert_change("2026-06-26", "2317", "鴻海", etf_code="00981A", prev_weight=0.04, is_removed=1)

    report = generate_signal_report("2026-06-26")

    assert "3363 上詮" in report
    assert "2317 鴻海" not in report
    assert "低權重移除已隱藏: 1" in report
