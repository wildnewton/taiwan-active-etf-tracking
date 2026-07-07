from datetime import date, datetime

import db
from models import ScrapeRun


def _scrape_run(status, *, error=None, rows_extracted=0, started_minute=0, data_date=None):
    return ScrapeRun(
        date=date(2026, 7, 3),
        data_date=data_date,
        etf_code="00980A",
        status=status,
        primary_source="moneydj_primary",
        primary_success=(status == "success"),
        moneydj_browser_used=False,
        official_fallback_used=False,
        official_success=False,
        rows_extracted=rows_extracted,
        stock_rows_extracted=rows_extracted,
        non_stock_rows_extracted=0,
        total_weight_all_rows=95.0 if status == "success" else 0.0,
        total_weight_stock_rows=95.0 if status == "success" else 0.0,
        source_url="https://example.test" if status == "success" else "",
        error=error,
        started_at=datetime(2026, 7, 3, 9, started_minute),
        finished_at=datetime(2026, 7, 3, 9, started_minute + 1),
    )


def _fetch_run():
    with db._connect() as conn:
        return conn.execute(
            """
            SELECT status, rows_extracted, error, primary_success, source_url, data_date
            FROM etf_scrape_runs
            WHERE date = '2026-07-03' AND etf_code = '00980A'
            """
        ).fetchone()


def test_failed_scrape_run_does_not_overwrite_existing_success():
    db.init_db(":memory:")

    db.insert_scrape_run(_scrape_run("success", rows_extracted=54, started_minute=1, data_date=date(2026, 7, 3)))
    db.insert_scrape_run(_scrape_run("failed", error="temporary outage", started_minute=2))

    row = _fetch_run()

    assert row[0] == "success"
    assert row[1] == 54
    assert row[2] is None
    assert row[3] == 1
    assert row[4] == "https://example.test"
    assert row[5] == "2026-07-03"


def test_success_scrape_run_replaces_existing_failure():
    db.init_db(":memory:")

    db.insert_scrape_run(_scrape_run("failed", error="temporary outage", started_minute=1))
    db.insert_scrape_run(_scrape_run("success", rows_extracted=54, started_minute=2, data_date=date(2026, 7, 3)))

    row = _fetch_run()

    assert row[0] == "success"
    assert row[1] == 54
    assert row[2] is None
    assert row[3] == 1
    assert row[5] == "2026-07-03"


def test_failed_scrape_run_can_update_existing_failure():
    db.init_db(":memory:")

    db.insert_scrape_run(_scrape_run("failed", error="first failure", started_minute=1))
    db.insert_scrape_run(_scrape_run("failed", error="second failure", started_minute=2))

    row = _fetch_run()

    assert row[0] == "failed"
    assert row[1] == 0
    assert row[2] == "second failure"
    assert row[3] == 0
    assert row[5] is None
