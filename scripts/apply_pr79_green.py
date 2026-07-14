from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_in_section(
    path: str,
    start: str,
    end: str,
    old: str,
    new: str,
) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    start_at = text.index(start)
    end_at = text.index(end, start_at)
    section = text[start_at:end_at]
    count = section.count(old)
    if count != 1:
        raise RuntimeError(f"{path}:{start}: expected one match, found {count}")
    text = text[:start_at] + section.replace(old, new, 1) + text[end_at:]
    file_path.write_text(text, encoding="utf-8")


def main() -> None:
    replace_once(
        "scripts/pipeline.py",
        """def _active_etfs_for_run() -> list[dict]:
    seed_etf_universe_from_file()
    return get_active_etfs()


def _run_daily_scrape_sync(db_path: str, scrape_fn: ScrapeFn) -> dict:
    init_db(db_path)
    active_etfs = _active_etfs_for_run()
    return _run_scrape_sync(db_path, active_etfs, scrape_fn, already_initialized=True)
""",
        """def _active_etfs_for_run(run_date: date) -> list[dict]:
    seed_etf_universe_from_file()
    return get_active_etfs(as_of_date=run_date)


def _run_daily_scrape_sync(db_path: str, scrape_fn: ScrapeFn) -> dict:
    init_db(db_path)
    run_at = _as_taipei_run_at(_current_run_at())
    active_etfs = _active_etfs_for_run(run_at.date())
    return _run_scrape_sync(
        db_path,
        active_etfs,
        scrape_fn,
        already_initialized=True,
        run_at=run_at,
    )
""",
    )

    replace_once(
        "scripts/pipeline.py",
        """    already_initialized: bool = False,
    use_trading_calendar: bool = True,
) -> dict:
    if not already_initialized:
        init_db(db_path)
    run_at = _as_taipei_run_at(_current_run_at())
""",
        """    already_initialized: bool = False,
    use_trading_calendar: bool = True,
    run_at: datetime | None = None,
) -> dict:
    if not already_initialized:
        init_db(db_path)
    run_at = _as_taipei_run_at(run_at or _current_run_at())
""",
    )

    replace_once(
        "scripts/pipeline.py",
        """    init_db(db_path)
    if etfs is None:
        etfs = _active_etfs_for_run()
    if run_date is None:
        run_at = _as_taipei_run_at(_current_run_at())
        run_date = run_at.date()
    else:
        run_at = datetime.combine(
            run_date,
            DATA_AVAILABILITY_CUTOFF,
            tzinfo=TAIPEI_TIMEZONE,
        )
""",
        """    init_db(db_path)
    if run_date is None:
        run_at = _as_taipei_run_at(_current_run_at())
        run_date = run_at.date()
    else:
        run_at = datetime.combine(
            run_date,
            DATA_AVAILABILITY_CUTOFF,
            tzinfo=TAIPEI_TIMEZONE,
        )
    if etfs is None:
        etfs = _active_etfs_for_run(run_date)
""",
    )

    replace_once(
        "scripts/report.py",
        "    expected_count = get_active_etf_count()\n    actual_count = _get_actual_etf_count(data_date)",
        "    expected_count = get_active_etf_count(as_of_date=data_date)\n    actual_count = _get_actual_etf_count(data_date)",
    )

    for start, end, date_column in (
        ("def _get_failed_etfs", "def _get_scrape_data_freshness", "sr.date"),
        ("def _get_scrape_data_freshness", "def _get_skipped_change_diagnostics", "sr.date"),
        ("def _get_skipped_change_diagnostics", "def _get_summary_stats", "d.date"),
    ):
        replace_in_section(
            "scripts/report.py",
            start,
            end,
            "AND u.retired = 0",
            f"AND u.retired = 0\n                      AND (u.listing_date IS NULL OR u.listing_date <= {date_column})",
        )

    replace_once(
        "tests/test_etf_universe.py",
        '        "isin",\n        "retired",',
        '        "isin",\n        "listing_date",\n        "retired",',
    )


if __name__ == "__main__":
    main()
