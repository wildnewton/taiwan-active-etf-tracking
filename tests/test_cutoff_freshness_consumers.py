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
            INSERT INTO etf_universe (code, name, retired, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (code, code, retired, f"{RUN_DATE}T00:00:00", f"{RUN_DATE}T00:00:00"),
        )


def _seed_scrape_run(
    code: str,
    *,
    run_date: str = RUN_DATE,
    data_date: str | None,
    status: str,
) -> None:
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
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_date,
                data_date,
                code,
                status,
                "moneydj_primary",
                1 if usable else 0,
                0,
                0,
                0,
                1 if usable else 0,
                1 if usable else 0,
                0,
                10.0 if usable else 0.0,
                10.0 if usable else 0.0,
                "https://example.test" if usable else None,
                None if usable else "timeout",
                f"{run_date}T21:00:00",
                f"{run_date}T21:00:01",
            ),
        )


def test_retry_selects_usable_rows_with_data_older_than_run_date():
    db.init_db(":memory:")
    for code in ("OLD_SUCCESS", "STALE", "FRESH", "FAILED", "RETIRED"):
        _seed_etf(code, retired=1 if code == "RETIRED" else 0)

    # Cutoff edge case: the morning row remains persisted as success even though
    # its data date is older than the report date.
    _seed_scrape_run("OLD_SUCCESS", data_date=PREVIOUS_DATE, status="success")
    _seed_scrape_run("STALE", data_date=PREVIOUS_DATE, status="stale")
    _seed_scrape_run("FRESH", data_date=RUN_DATE, status="success")
    _seed_scrape_run("FAILED", data_date=PREVIOUS_DATE, status="failed")
    _seed_scrape_run("RETIRED", data_date=PREVIOUS_DATE, status="success")

    assert get_stale_scrape_runs(RUN_DATE) == [
        {"etf_code": "OLD_SUCCESS", "data_date": PREVIOUS_DATE},
        {"etf_code": "STALE", "data_date": PREVIOUS_DATE},
    ]


def _seed_success_set(run_date: str, data_date: str, *, count: int = 16) -> None:
    for index in range(count):
        _seed_scrape_run(
            f"{run_date[-2:]}_{index:02d}",
            run_date=run_date,
            data_date=data_date,
            status="success",
        )


def test_valid_date_selection_requires_success_data_date_to_match_run_date():
    db.init_db(":memory:")

    _seed_success_set(OLDER_DATE, OLDER_DATE)
    _seed_success_set(PREVIOUS_DATE, PREVIOUS_DATE)
    _seed_success_set(RUN_DATE, PREVIOUS_DATE)

    assert get_latest_valid_date() == PREVIOUS_DATE
    assert get_previous_valid_date(RUN_DATE) == PREVIOUS_DATE
