import db
from changes import get_latest_valid_date, get_previous_valid_date
from retry_stale_scrapes import get_stale_scrape_runs


RUN_DATE = "2026-07-15"
PREVIOUS_DATE = "2026-07-14"
OLDER_DATE = "2026-07-13"


def _seed_etf(code: str, *, retired: int = 0) -> None:
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_universe (
                code, name, listing_date, retired, created_at, updated_at
            ) VALUES (?, ?, '2026-07-01', ?, ?, ?)
            """,
            (code, code, retired, f"{RUN_DATE}T00:00:00", f"{RUN_DATE}T00:00:00"),
        )


def _seed_holding(code: str, data_date: str) -> None:
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_daily_holdings (
                date, etf_code, asset_name, asset_type, stock_code, stock_name,
                shares, weight_pct, source_url, source_type, extraction_method,
                scraped_at
            ) VALUES (?, ?, '台積電(2330.TW)', 'stock', '2330', '台積電',
                      1000, 10.0, 'https://example.test', 'moneydj_primary',
                      'test', ?)
            """,
            (data_date, code, f"{data_date}T21:00:00"),
        )


def _seed_scrape_run(code: str, *, run_date: str, data_date: str, status: str) -> None:
    usable = status in {"success", "stale"}
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_scrape_runs (
                date, data_date, etf_code, status, primary_source, primary_success,
                moneydj_browser_used, official_fallback_used, official_success,
                rows_extracted, stock_rows_extracted, non_stock_rows_extracted,
                total_weight_all_rows, total_weight_stock_rows, source_url, error,
                started_at, finished_at
            ) VALUES (?, ?, ?, ?, 'moneydj_primary', ?, 0, 0, 0, ?, ?, 0,
                      ?, ?, 'https://example.test', NULL, ?, ?)
            """,
            (
                run_date,
                data_date,
                code,
                status,
                1 if usable else 0,
                1 if usable else 0,
                1 if usable else 0,
                10.0 if usable else 0.0,
                10.0 if usable else 0.0,
                f"{run_date}T21:00:00",
                f"{run_date}T21:00:01",
            ),
        )


def test_retry_selects_only_canonical_stale_rows():
    db.init_db(":memory:")
    for code in ("OLD_SUCCESS", "STALE", "FAILED", "RETIRED"):
        _seed_etf(code, retired=1 if code == "RETIRED" else 0)
    _seed_scrape_run(
        "OLD_SUCCESS", run_date=RUN_DATE, data_date=PREVIOUS_DATE, status="success"
    )
    _seed_scrape_run(
        "STALE", run_date=RUN_DATE, data_date=PREVIOUS_DATE, status="stale"
    )
    _seed_scrape_run(
        "FAILED", run_date=RUN_DATE, data_date=PREVIOUS_DATE, status="failed"
    )
    _seed_scrape_run(
        "RETIRED", run_date=RUN_DATE, data_date=PREVIOUS_DATE, status="stale"
    )

    assert get_stale_scrape_runs(RUN_DATE) == [
        {"etf_code": "STALE", "data_date": PREVIOUS_DATE}
    ]


def test_valid_date_selection_uses_snapshot_dates_not_scrape_run_dates():
    # Regression: scrape-run dates must not become holdings chronology.
    db.init_db(":memory:")
    for code in ("A", "B"):
        _seed_etf(code)
        _seed_holding(code, OLDER_DATE)
        _seed_scrape_run(
            code, run_date=OLDER_DATE, data_date=OLDER_DATE, status="success"
        )
        _seed_holding(code, PREVIOUS_DATE)
        _seed_scrape_run(
            code, run_date=RUN_DATE, data_date=PREVIOUS_DATE, status="stale"
        )

    assert get_latest_valid_date() == PREVIOUS_DATE
    assert get_previous_valid_date(RUN_DATE) == PREVIOUS_DATE
