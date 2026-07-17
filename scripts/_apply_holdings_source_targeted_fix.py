from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def update(path, old, new, label):
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


update(
    "scripts/db.py",
    '''    params = [etf_code, etf_code]
    if before_date is not None:
        before_date = _serialize(before_date)
        params.extend([before_date, before_date])
''',
    '''    if before_date is not None:
        before_date = _serialize(before_date)
        params = [etf_code, before_date, etf_code, before_date]
    else:
        params = [etf_code, etf_code]
''',
    "latest snapshot query parameters",
)

update(
    "tests/test_signal_report.py",
    '''    db.init_db(":memory:")
    ensure_signal_table()

    # Insert holdings for two dates
''',
    '''    db.init_db(":memory:")
    ensure_signal_table()
    seed_universe([("00980A", 0)])

    # Insert holdings for two dates
''',
    "latest report date universe",
)

print("Applied final targeted holdings fixes")
