import sqlite3
from datetime import date, datetime

import db
import pipeline
import report
import retry_stale_scrapes
from changes import get_latest_valid_date
from models import HoldingRow


def _seed_universe(code, *, listing_date="2026-07-01", retired=0, last_active_date=None):
    now = datetime.now().isoformat()
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_universe (
                code, name, listing_date, retired, first_seen_date,
                last_active_date, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                code,
                code,
                listing_date,
                retired,
                listing_date,
                last_active_date,
                now,
                now,
            ),
        )


def _insert_holding(data_date, etf_code, stock_code="2330"):
    db.insert_holdings(
        [
            HoldingRow(
                date=date.fromisoformat(data_date),
                etf_code=etf_code,
                asset_name=f"Stock {stock_code}",
                asset_type="stock",
                stock_code=stock_code,
                stock_name=f"Stock {stock_code}",
                shares=100.0,
                weight_pct=10.0,
                source_url="https://example.com",
                source_type="moneydj_primary",
                extraction_method="test",
                scraped_at=datetime.now(),
            )
        ]
    )


def test_init_db_removes_legacy_scrape_run_table(tmp_path):
    db_path = tmp_path / "holdings.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE etf_scrape_runs (date TEXT, etf_code TEXT)")
        conn.execute("INSERT INTO etf_scrape_runs VALUES ('2026-07-15', 'A')")

    db.init_db(db_path)

    with db._connect() as conn:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='etf_scrape_runs'"
        ).fetchone()
    assert table is None


def test_new_database_has_no_scrape_run_table(tmp_path):
    db.init_db(tmp_path / "holdings.sqlite")

    with db._connect() as conn:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='etf_scrape_runs'"
        ).fetchone()
    assert table is None


def test_preexisting_target_snapshot_does_not_require_scrape_run(tmp_path):
    db.init_db(tmp_path / "holdings.sqlite")
    _seed_universe("A")
    _insert_holding("2026-07-15", "A")

    preexisting, missing = pipeline._partition_preexisting_successes(
        [{"code": "A"}],
        date(2026, 7, 15),
    )

    assert preexisting == [{"code": "A"}]
    assert missing == []


def test_retry_candidates_are_derived_from_missing_target_holdings(tmp_path):
    db.init_db(tmp_path / "holdings.sqlite")
    _seed_universe("A")
    _seed_universe("B")
    _seed_universe("FUTURE", listing_date="2026-07-20")
    _seed_universe("RETIRED", retired=1, last_active_date="2026-07-14")
    _insert_holding("2026-07-14", "A")
    _insert_holding("2026-07-15", "B")

    candidates = retry_stale_scrapes.get_retry_candidates("2026-07-15")

    assert candidates == [{"etf_code": "A", "data_date": "2026-07-14"}]


def test_target_snapshot_completion_removes_retry_candidate(tmp_path):
    db.init_db(tmp_path / "holdings.sqlite")
    _seed_universe("A")
    _insert_holding("2026-07-14", "A")
    assert retry_stale_scrapes.get_retry_candidates("2026-07-15") == [
        {"etf_code": "A", "data_date": "2026-07-14"}
    ]

    _insert_holding("2026-07-15", "A")

    assert retry_stale_scrapes.get_retry_candidates("2026-07-15") == []


def test_report_quality_is_derived_from_holdings_and_universe(tmp_path):
    db.init_db(tmp_path / "holdings.sqlite")
    _seed_universe("A")
    _seed_universe("B")
    _seed_universe("C")
    _insert_holding("2026-07-15", "A")
    _insert_holding("2026-07-14", "B")

    quality = report._get_data_quality("2026-07-15", quality_run_date="2026-07-15")

    assert quality["actual_count"] == 1
    assert quality["expected_count"] == 3
    assert quality["missing_etfs"] == ["B", "C"]
    assert quality["scrape_freshness"] == {
        "fresh": [{"etf_code": "A", "data_date": "2026-07-15"}],
        "stale": [{"etf_code": "B", "data_date": "2026-07-14"}],
        "unknown": [{"etf_code": "C", "data_date": None}],
    }


def test_historical_completeness_uses_candidate_date_universe(tmp_path):
    db.init_db(tmp_path / "holdings.sqlite")
    _seed_universe("A")
    _seed_universe(
        "B",
        retired=1,
        last_active_date="2026-07-20",
    )
    _insert_holding("2026-07-15", "A")

    assert get_latest_valid_date(min_success_ratio=1.0) is None
