from pathlib import Path

p = Path(__file__).with_name("pipeline.py")
s = p.read_text()

def r(old, new):
    global s
    assert s.count(old) == 1, old[:60]
    s = s.replace(old, new, 1)

r("""def run_selected_scrape_with_browser(
    db_path: str,
    etf_codes: list[str],
    run_date=None,
) -> dict:
    return asyncio.run(run_selected_scrape_with_browser_async(db_path, etf_codes, run_date=_coerce_run_date(run_date)))
""", """def run_selected_scrape_with_browser(
    db_path: str,
    etf_codes: list[str],
    run_date=None,
    target_date=None,
) -> dict:
    if target_date is None:
        target_date = run_date
    return asyncio.run(run_selected_scrape_with_browser_async(
        db_path, etf_codes,
        run_date=_coerce_run_date(run_date),
        target_date=_coerce_run_date(target_date),
    ))
""")
r("""    page=None,
    run_date=None,
) -> dict:
    selected_etfs = [{"code": code} for code in etf_codes]
    run_date = _coerce_run_date(run_date)
""", """    page=None,
    run_date=None,
    target_date=None,
) -> dict:
    selected_etfs = [{"code": code} for code in etf_codes]
    run_date = _coerce_run_date(run_date)
    target_date = _coerce_run_date(target_date if target_date is not None else run_date)
""")
s = s.replace("run_date=run_date,\n            use_trading_calendar=False,", "run_date=run_date,\n            expected_data_date=target_date,\n            use_trading_calendar=False,", 1)
s = s.replace("run_date=run_date,\n                    use_trading_calendar=False,", "run_date=run_date,\n                    expected_data_date=target_date,\n                    use_trading_calendar=False,", 1)
r("""    run_date=None,
    use_trading_calendar: bool = True,
    skip_existing_snapshot: bool = True,
) -> dict:
""", """    run_date=None,
    expected_data_date=None,
    use_trading_calendar: bool = True,
    skip_existing_snapshot: bool = True,
) -> dict:
""")
r("""        run_date=run_date,
        use_trading_calendar=use_trading_calendar,
        skip_existing_snapshot=skip_existing_snapshot,
""", """        run_date=run_date,
        expected_data_date=expected_data_date,
        use_trading_calendar=use_trading_calendar,
        skip_existing_snapshot=skip_existing_snapshot,
""")
r("""    run_at: datetime | None = None,
    run_date=None,
    skip_existing_snapshot: bool = True,
""", """    run_at: datetime | None = None,
    run_date=None,
    expected_data_date=None,
    skip_existing_snapshot: bool = True,
""")
r("""    expected_data_date = _expected_data_date_for_run(run_at, use_trading_calendar)
""", """    if expected_data_date is None:
        expected_data_date = _expected_data_date_for_run(run_at, use_trading_calendar)
    else:
        expected_data_date = _coerce_run_date(expected_data_date)
""")
p.write_text(s)

p = Path(__file__).with_name("retry_stale_scrapes.py")
s = p.read_text()
old = """        run_date=target_date,
"""
assert s.count(old) == 1
p.write_text(s.replace(old, """        target_date=target_date,
""", 1))
