from unittest.mock import patch

import pytest

import changes
import db
import nightly_pipeline
import report
from retry_stale_scrapes import get_stale_scrape_runs


CURRENT_DATE = "2026-07-15"
PARTIAL_DATE = "2026-07-14"
COMPLETE_PREVIOUS_DATE = "2026-07-13"


def _seed_etf(code: str, *, listing_date: str = "2026-07-01", retired: int = 0) -> None:
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
            ) VALUES (?, ?, ?, 'stock', '2330', '台積電', 1000, 90.0,
                      'https://example.test', 'moneydj_primary', 'test', ?)
            """,
            (data_date, code, f"台積電({code})", f"{data_date}T21:00:00"),
        )


def _seed_scrape_run(
    run_date: str,
    code: str,
    *,
    status: str,
    data_date: str | None,
) -> None:
    usable = status in {"success", "stale"}
    with db._connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO etf_scrape_runs (
                date, data_date, etf_code, status, primary_source, primary_success,
                moneydj_browser_used, official_fallback_used, official_success,
                rows_extracted, stock_rows_extracted, non_stock_rows_extracted,
                total_weight_all_rows, total_weight_stock_rows, source_url, error,
                started_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, 0, 0, 0, ?, ?, 0, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_date,
                data_date,
                code,
                status,
                "moneydj_primary" if usable else "none",
                1 if usable else 0,
                1 if usable else 0,
                1 if usable else 0,
                90.0 if usable else 0.0,
                90.0 if usable else 0.0,
                "https://example.test" if usable else None,
                None if usable else "timeout",
                f"{run_date}T21:00:00",
                f"{run_date}T21:00:01",
            ),
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
            {
                "expected_data_date": CURRENT_DATE,
                "data_date_min": CURRENT_DATE,
                "data_date_max": CURRENT_DATE,
            },
            ":memory:",
        )


def test_quality_warnings_do_not_import_failures_from_holdings_date():
    db.init_db(":memory:")
    _seed_etf("A")
    _seed_holding(PARTIAL_DATE, "A")
    _seed_scrape_run(PARTIAL_DATE, "A", status="failed", data_date=None)
    _seed_scrape_run(CURRENT_DATE, "A", status="success", data_date=CURRENT_DATE)

    quality = report._get_data_quality(
        PARTIAL_DATE,
        quality_run_date=CURRENT_DATE,
    )

    assert quality["failed_etfs"] == []
    assert not any("抓取失敗" in warning for warning in quality["warnings"])


def test_quality_failure_is_rendered_once_from_quality_run():
    db.init_db(":memory:")
    _seed_etf("A")
    _seed_holding(CURRENT_DATE, "A")
    _seed_scrape_run(CURRENT_DATE, "A", status="failed", data_date=None)

    rendered = "\n".join(
        report._render_data_quality(
            report._get_data_quality(CURRENT_DATE, quality_run_date=CURRENT_DATE)
        )
    )

    assert rendered.count("抓取失敗") == 1


def test_retry_excludes_stale_rows_for_prelisting_etfs():
    db.init_db(":memory:")
    _seed_etf("A", listing_date="2026-07-20")
    _seed_scrape_run(CURRENT_DATE, "A", status="stale", data_date=PARTIAL_DATE)

    assert get_stale_scrape_runs(CURRENT_DATE) == []


def test_valid_date_selection_sorts_candidates_in_code():
    class FakeResult:
        def fetchall(self):
            return [
                (COMPLETE_PREVIOUS_DATE, 2, 2, 2),
                (PARTIAL_DATE, 2, 2, 2),
                (CURRENT_DATE, 2, 2, 2),
            ]

    class FakeConnection:
        def execute(self, *_args, **_kwargs):
            return FakeResult()

    class FakeContext:
        def __enter__(self):
            return FakeConnection()

        def __exit__(self, exc_type, exc, tb):
            return False

    with patch("changes.db._connect", return_value=FakeContext()):
        assert changes.get_latest_valid_date(min_success_ratio=1.0) == CURRENT_DATE
