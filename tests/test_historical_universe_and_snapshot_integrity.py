from datetime import datetime
from unittest.mock import patch

import pytest

import db
import report
from changes import detect_holding_changes, get_latest_valid_date
from etf_universe import get_eligible_etf_codes, retire_etf
from retry_stale_scrapes import retry_missing_holdings
from snapshot_validation import validate_snapshot_rows as real_validate_snapshot_rows


pytestmark = pytest.mark.usefixtures("compact_snapshot_validation")

TARGET_DATE = "2026-07-15"
PREVIOUS_DATE = "2026-07-14"
EXECUTION_DATE = "2026-07-17"
VALID_STOCKS = [
    ("2301", "光寶科"),
    ("2303", "聯電"),
    ("2308", "台達電"),
    ("2317", "鴻海"),
    ("2330", "台積電"),
]


def _seed_etf(
    code: str,
    *,
    listing_date: str | None = "2026-07-01",
    retired: int = 0,
    official_logic: str | None = None,
) -> None:
    now = datetime.now().isoformat()
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_universe (
                code, name, issuer, listing_date, retired,
                first_seen_date, official_logic,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                code,
                code,
                f"Issuer-{code}",
                listing_date,
                retired,
                listing_date,
                official_logic,
                now,
                now,
            ),
        )


def _insert_snapshot(
    data_date: str,
    etf_code: str,
    *,
    stock_weight: float = 100.0,
    non_stock_weight: float = 0.0,
    shares: float = 100.0,
) -> None:
    now = datetime.now().isoformat()
    with db._connect() as conn:
        if stock_weight:
            conn.execute(
                """
                INSERT INTO etf_daily_holdings (
                    date, etf_code, asset_name, asset_type, stock_code, stock_name,
                    shares, weight_pct, source_url, source_type,
                    extraction_method, scraped_at
                ) VALUES (?, ?, '台積電', 'stock', '2330', '台積電', ?, ?,
                          'https://example.test', 'moneydj_primary', 'test', ?)
                """,
                (data_date, etf_code, shares, stock_weight, now),
            )
        if non_stock_weight:
            conn.execute(
                """
                INSERT INTO etf_daily_non_stock_assets (
                    date, etf_code, asset_name, asset_type, weight_pct,
                    source_url, source_type, extraction_method, scraped_at
                ) VALUES (?, ?, '現金', 'cash', ?, 'https://example.test',
                          'moneydj_primary', 'test', ?)
                """,
                (data_date, etf_code, non_stock_weight, now),
            )


def _insert_valid_snapshot(
    data_date: str,
    etf_code: str,
    *,
    total_stock_weight: float,
    non_stock_weight: float = 0.0,
) -> None:
    now = datetime.now().isoformat()
    per_stock_weight = total_stock_weight / len(VALID_STOCKS)
    with db._connect() as conn:
        conn.executemany(
            """
            INSERT INTO etf_daily_holdings (
                date, etf_code, asset_name, asset_type, stock_code, stock_name,
                shares, weight_pct, source_url, source_type,
                extraction_method, scraped_at
            ) VALUES (?, ?, ?, 'stock', ?, ?, 100, ?,
                      'https://example.test', 'moneydj_primary', 'test', ?)
            """,
            [
                (
                    data_date,
                    etf_code,
                    f"{name}({code}.TW)",
                    code,
                    name,
                    per_stock_weight,
                    now,
                )
                for code, name in VALID_STOCKS
            ],
        )
        if non_stock_weight:
            conn.execute(
                """
                INSERT INTO etf_daily_non_stock_assets (
                    date, etf_code, asset_name, asset_type, weight_pct,
                    source_url, source_type, extraction_method, scraped_at
                ) VALUES (?, ?, '現金', 'cash', ?, 'https://example.test',
                          'moneydj_primary', 'test', ?)
                """,
                (data_date, etf_code, non_stock_weight, now),
            )


def test_snapshot_exists_uses_structural_validity_not_total_weight(
    monkeypatch, strict_snapshot_validation
):
    monkeypatch.setattr(db, "validate_snapshot_rows", real_validate_snapshot_rows)
    db.init_db(":memory:")
    _seed_etf("A")

    assert db.snapshot_exists(TARGET_DATE, "A") is False

    _insert_valid_snapshot(TARGET_DATE, "A", total_stock_weight=90.0)
    assert db.snapshot_exists(TARGET_DATE, "A") is True

    with db._connect() as conn:
        conn.execute("DELETE FROM etf_daily_holdings")
    _insert_valid_snapshot(TARGET_DATE, "A", total_stock_weight=500.0)
    assert db.snapshot_exists(TARGET_DATE, "A") is True

    with db._connect() as conn:
        conn.execute("DELETE FROM etf_daily_holdings")
    for code, name in VALID_STOCKS[:4]:
        with db._connect() as conn:
            conn.execute(
                """
                INSERT INTO etf_daily_holdings (
                    date, etf_code, asset_name, asset_type, stock_code, stock_name,
                    shares, weight_pct, source_url, source_type,
                    extraction_method, scraped_at
                ) VALUES (?, 'A', ?, 'stock', ?, ?, 100, 25,
                          'https://example.test', 'moneydj_primary', 'test', ?)
                """,
                (TARGET_DATE, f"{name}({code}.TW)", code, name, datetime.now().isoformat()),
            )
    assert db.snapshot_exists(TARGET_DATE, "A") is False


def test_snapshot_integrity_counts_stock_and_non_stock_rows_together(
    monkeypatch, strict_snapshot_validation
):
    monkeypatch.setattr(db, "validate_snapshot_rows", real_validate_snapshot_rows)
    db.init_db(":memory:")
    _seed_etf("A")
    _insert_valid_snapshot(
        TARGET_DATE,
        "A",
        total_stock_weight=85.0,
        non_stock_weight=15.0,
    )

    assert db.snapshot_exists(TARGET_DATE, "A") is True


def test_non_stock_only_rows_do_not_form_a_holdings_snapshot(
    monkeypatch, strict_snapshot_validation
):
    monkeypatch.setattr(db, "validate_snapshot_rows", real_validate_snapshot_rows)
    db.init_db(":memory:")
    _seed_etf("A")
    _insert_snapshot(TARGET_DATE, "A", stock_weight=0.0, non_stock_weight=100.0)

    assert db.snapshot_exists(TARGET_DATE, "A") is False


def test_candidate_date_eligibility_uses_holdings_cutoff_and_excludes_scope():
    db.init_db(":memory:")
    _seed_etf("ACTIVE")
    _seed_etf("RETIRED_AFTER", retired=1)
    _seed_etf("RETIRED_BEFORE", retired=1)
    _seed_etf("FUTURE", listing_date="2026-07-20")
    _seed_etf(
        "OUT_OF_SCOPE",
        retired=1,
        official_logic="excluded_from_taiwan_stock_universe",
    )
    _insert_snapshot(TARGET_DATE, "RETIRED_AFTER")
    _insert_snapshot(PREVIOUS_DATE, "RETIRED_BEFORE")
    _insert_snapshot(TARGET_DATE, "OUT_OF_SCOPE")

    assert get_eligible_etf_codes(TARGET_DATE) == ["ACTIVE", "RETIRED_AFTER"]
    assert get_eligible_etf_codes("2026-07-16") == ["ACTIVE"]


def test_retired_etf_is_analyzed_through_its_latest_holdings_date():
    db.init_db(":memory:")
    _seed_etf("HISTORICAL", retired=1)
    _insert_snapshot(PREVIOUS_DATE, "HISTORICAL", shares=100.0)
    _insert_snapshot(TARGET_DATE, "HISTORICAL", shares=110.0)

    summary = detect_holding_changes(TARGET_DATE, PREVIOUS_DATE)

    assert summary["ok"] is True
    assert summary["rows"] == 1
    with db._connect() as conn:
        row = conn.execute(
            """
            SELECT shares_delta_1d
            FROM etf_holding_changes
            WHERE date = ? AND etf_code = 'HISTORICAL'
            """,
            (TARGET_DATE,),
        ).fetchone()
    assert row == (10.0,)


def test_report_change_diagnostics_use_candidate_date_eligibility():
    db.init_db(":memory:")
    _seed_etf("HISTORICAL", retired=1)
    _seed_etf(
        "OUT_OF_SCOPE",
        retired=1,
        official_logic="trades_offshore_instruments=true",
    )
    _insert_snapshot(TARGET_DATE, "HISTORICAL")
    _insert_snapshot(TARGET_DATE, "OUT_OF_SCOPE")
    with db._connect() as conn:
        for code in ("HISTORICAL", "OUT_OF_SCOPE"):
            conn.execute(
                """
                INSERT INTO etf_change_diagnostics (
                    date, prev_date, etf_code, status, reason,
                    current_source_type, previous_source_type, created_at
                ) VALUES (?, ?, ?, 'skipped', 'missing_current_source',
                          'moneydj_primary', 'moneydj_primary', ?)
                """,
                (TARGET_DATE, PREVIOUS_DATE, code, "2026-07-17T01:00:00"),
            )

    rows = report._get_skipped_change_diagnostics(TARGET_DATE)

    assert [row["etf_code"] for row in rows] == ["HISTORICAL"]


def test_stray_holdings_do_not_make_an_empty_candidate_universe_valid():
    db.init_db(":memory:")
    _seed_etf("FUTURE", listing_date="2026-07-20")
    _insert_snapshot(TARGET_DATE, "FUTURE")

    coverage = db.get_target_snapshot_coverage(TARGET_DATE)

    assert coverage["expected_count"] == 0
    assert coverage["actual_count"] == 0
    assert coverage["actual_etf_codes"] == []
    assert get_latest_valid_date() is None


def test_retry_summary_separates_execution_date_from_target_date():
    candidate = [{"etf_code": "A", "data_date": PREVIOUS_DATE}]
    with patch("retry_stale_scrapes.db.init_db"), patch(
        "retry_stale_scrapes.get_retry_candidates",
        side_effect=[candidate, candidate],
    ), patch(
        "retry_stale_scrapes.run_selected_scrape_with_browser",
        return_value={"date": EXECUTION_DATE, "expected_data_date": TARGET_DATE},
    ):
        summary = retry_missing_holdings(
            db_path=":memory:",
            target_date=TARGET_DATE,
        )

    assert summary["run_date"] == EXECUTION_DATE
    assert summary["target_date"] == TARGET_DATE
    assert "date" not in summary


def test_manual_retirement_uses_existing_holdings_as_historical_cutoff():
    db.init_db(":memory:")
    _seed_etf("A")
    _insert_snapshot(TARGET_DATE, "A")

    retire_etf("A", reason="confirmed retired")

    assert "A" in get_eligible_etf_codes(TARGET_DATE)
    assert "A" not in get_eligible_etf_codes(EXECUTION_DATE)
