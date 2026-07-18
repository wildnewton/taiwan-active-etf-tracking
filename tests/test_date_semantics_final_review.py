from unittest.mock import patch

import pytest

import changes
import db
import nightly_pipeline
import report
from retry_stale_scrapes import get_retry_candidates


CURRENT_DATE = "2026-07-15"
PARTIAL_DATE = "2026-07-14"
COMPLETE_PREVIOUS_DATE = "2026-07-13"


def _seed_etf(
    code: str,
    *,
    listing_date: str = "2026-07-01",
    retired: int = 0,
) -> None:
    with db._connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO etf_universe (
                code, name, listing_date, retired, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                code,
                code,
                listing_date,
                retired,
                "2026-07-01T00:00:00",
                "2026-07-01T00:00:00",
            ),
        )


def _seed_holding(data_date: str, code: str) -> None:
    with db._connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO etf_daily_holdings (
                date, etf_code, asset_name, asset_type, stock_code, stock_name,
                shares, weight_pct, source_url, source_type, extraction_method,
                scraped_at
            ) VALUES (?, ?, ?, 'stock', '2330', '台積電', 1000, 100.0,
                      'https://example.test', 'moneydj_primary', 'test', ?)
            """,
            (data_date, code, f"台積電({code})", f"{data_date}T21:00:00"),
        )


def test_report_previous_date_uses_complete_holdings_chronology():
    db.init_db(":memory:")
    for code in ("A", "B"):
        _seed_etf(code)
        _seed_holding(COMPLETE_PREVIOUS_DATE, code)
        _seed_holding(CURRENT_DATE, code)
    _seed_holding(PARTIAL_DATE, "A")

    assert changes.get_previous_valid_date(CURRENT_DATE, min_success_ratio=1.0) == (
        COMPLETE_PREVIOUS_DATE
    )
    assert report._get_previous_holdings_date(CURRENT_DATE) == COMPLETE_PREVIOUS_DATE


def test_report_and_nightly_do_not_fall_back_to_partial_snapshot():
    db.init_db(":memory:")
    for code in ("A", "B", "C"):
        _seed_etf(code)
    _seed_holding(CURRENT_DATE, "A")

    assert changes.get_latest_valid_date() is None
    assert report._get_latest_holdings_date() is None
    with pytest.raises(RuntimeError, match="persisted holdings date mismatch"):
        nightly_pipeline._resolve_target_data_date(
            {"expected_data_date": CURRENT_DATE},
            ":memory:",
        )


def test_retry_excludes_prelisting_and_historically_retired_etfs():
    db.init_db(":memory:")
    _seed_etf("ACTIVE")
    _seed_etf("FUTURE", listing_date="2026-07-20")
    _seed_etf("RETIRED", retired=1)
    _seed_holding(PARTIAL_DATE, "RETIRED")

    assert get_retry_candidates(CURRENT_DATE) == [
        {"etf_code": "ACTIVE", "data_date": None}
    ]


def test_valid_date_selection_sorts_candidates_in_code():
    class FakeResult:
        def fetchall(self):
            return [
                (COMPLETE_PREVIOUS_DATE,),
                (PARTIAL_DATE,),
                (CURRENT_DATE,),
            ]

    class FakeConnection:
        def execute(self, *_args, **_kwargs):
            return FakeResult()

    class FakeContext:
        def __enter__(self):
            return FakeConnection()

        def __exit__(self, exc_type, exc, tb):
            return False

    def coverage(date_value):
        return {
            "expected_count": 2,
            "actual_count": 2,
        }

    with patch("changes.db._connect", return_value=FakeContext()), patch(
        "changes.db.get_target_snapshot_coverage", side_effect=coverage
    ):
        assert changes.get_latest_valid_date(min_success_ratio=1.0) == CURRENT_DATE
