from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def write(path, text):
    (ROOT / path).write_text(text, encoding="utf-8")


def between(text, start, end, replacement, label):
    a = text.find(start)
    b = text.find(end, a + len(start))
    if a < 0 or b < 0:
        raise RuntimeError(f"{label}: markers not found")
    return text[:a] + replacement + text[b:]


# Backfill fixtures need the candidate-date universe they exercise.
path = "tests/test_backfill_changes.py"
text = read(path)
needle = '''def insert_holding(date, etf_code, stock_code, stock_name, shares, weight_pct):
    with db._connect() as conn:
        conn.execute(
'''
replacement = '''def insert_holding(date, etf_code, stock_code, stock_name, shares, weight_pct):
    with db._connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO etf_universe (
                code, name, issuer, listing_date, retired, created_at, updated_at
            ) VALUES (?, ?, 'Nomura', '2026-01-01', 0,
                      '2026-01-01T00:00:00', '2026-01-01T00:00:00')
            """,
            (etf_code, etf_code),
        )
        conn.execute(
'''
if text.count(needle) != 1:
    raise RuntimeError("backfill insert_holding marker")
write(path, text.replace(needle, replacement, 1))


# Async worker tests only need the streamlined summary recorder signature.
path = "tests/test_bounded_async_scraping.py"
text = read(path).replace(
    "def record_result(summary, etf_code, run_date, expected_date, started_at, finished_at, result):",
    "def record_result(summary, etf_code, run_date, expected_date, result):",
)
write(path, text)


# Core pipeline behavior: preserve validation/summary/holdings coverage, remove attempt-state assertions.
write(
    "tests/test_pipeline.py",
    '''from contextlib import contextmanager
from datetime import date, datetime
from unittest.mock import AsyncMock, patch

import pytest

import db
import pipeline
from pipeline import run_daily_scrape, run_daily_scrape_with_browser_async


RUN_DATE = date(2026, 6, 22)
NEXT_RUN_DATE = date(2026, 6, 23)
TEST_ETF_CODES = ["00980A", "00981A", "00982A"]
TEST_ETFS = [{"code": code} for code in TEST_ETF_CODES]


class FixedDate(date):
    @classmethod
    def today(cls):
        return cls(RUN_DATE.year, RUN_DATE.month, RUN_DATE.day)


class NextRunDate(date):
    @classmethod
    def today(cls):
        return cls(NEXT_RUN_DATE.year, NEXT_RUN_DATE.month, NEXT_RUN_DATE.day)


@contextmanager
def _patch_run_date(date_cls=FixedDate):
    run_at = datetime.combine(
        date_cls.today(),
        pipeline.DATA_AVAILABILITY_CUTOFF,
        tzinfo=pipeline.TAIPEI_TIMEZONE,
    )
    with patch("pipeline.date", date_cls), patch(
        "pipeline._current_run_at", return_value=run_at
    ):
        yield


def _patch_active_etfs():
    return patch("pipeline._active_etfs_for_run", return_value=TEST_ETFS)


def make_row(etf_code, row_date="2026/06/22", source_type="moneydj_primary"):
    return {
        "date": row_date,
        "etf_code": etf_code,
        "asset_name": "台積電(2330.TW)",
        "asset_type": "stock",
        "stock_code": "2330",
        "stock_name": "台積電",
        "shares": 1000,
        "weight_pct": 10.0,
        "source_url": "https://example.test",
        "source_type": source_type,
        "extraction_method": "requests_bs4",
    }


def make_success(etf_code, source_type="moneydj_primary", row_date="2026/06/22"):
    row = make_row(etf_code, row_date=row_date, source_type=source_type)
    return {
        "ok": True,
        "reason": "ok",
        "all_rows": [row],
        "stock_rows": [row],
        "non_stock_rows": [],
        "source_url": "https://example.test",
        "source_type": source_type,
        "total_weight_all_rows": 10.0,
        "total_weight_stock_rows": 10.0,
    }


def make_failure(reason="all sources failed"):
    return {
        "ok": False,
        "reason": reason,
        "all_rows": [],
        "stock_rows": [],
        "non_stock_rows": [],
        "source_url": "",
        "source_type": "",
        "total_weight_all_rows": 0.0,
        "total_weight_stock_rows": 0.0,
    }


def test_run_daily_scrape_all_success():
    with _patch_run_date(), _patch_active_etfs(), patch(
        "pipeline.scrape_holdings",
        side_effect=lambda code, target_date=None: make_success(code),
    ) as scrape, patch("pipeline.init_db") as init_db, patch(
        "pipeline.replace_daily_snapshot", return_value={"inserted": True}
    ) as replace_snapshot:
        summary = run_daily_scrape(":memory:")

    assert scrape.call_count == 3
    assert summary["date"] == "2026-06-22"
    assert summary["data_freshness"] == {"fresh": 3, "stale": 0, "unknown": 0}
    assert summary["moneydj_success"] == 3
    assert summary["failed"] == 0
    assert summary["data_date_min"] == "2026-06-22"
    assert summary["data_date_max"] == "2026-06-22"
    init_db.assert_called_once_with(":memory:")
    assert replace_snapshot.call_count == 3


@pytest.mark.asyncio
async def test_run_daily_scrape_with_browser_async_uses_browser_decision_tree():
    page = object()
    scraper = AsyncMock(
        side_effect=lambda code, page_arg, target_date=None: make_success(
            code, source_type="moneydj_browser"
        )
    )
    with _patch_run_date(), _patch_active_etfs(), patch(
        "pipeline.scrape_holdings_with_browser_async", scraper
    ), patch("pipeline.init_db"), patch(
        "pipeline.replace_daily_snapshot", return_value={"inserted": True}
    ) as replace_snapshot:
        summary = await run_daily_scrape_with_browser_async(":memory:", page=page)

    assert [call.args[0] for call in scraper.await_args_list] == TEST_ETF_CODES
    assert {call.args[1] for call in scraper.await_args_list} == {page}
    assert summary["data_freshness"] == {"fresh": 3, "stale": 0, "unknown": 0}
    assert replace_snapshot.call_count == 3


def test_run_daily_scrape_some_fail():
    failed_codes = {"00980A", "00981A"}

    def fake_scrape(code, target_date=None):
        return make_failure("blocked") if code in failed_codes else make_success(code)

    with _patch_run_date(), _patch_active_etfs(), patch(
        "pipeline.scrape_holdings", side_effect=fake_scrape
    ), patch("pipeline.init_db"), patch(
        "pipeline.replace_daily_snapshot", return_value={"inserted": True}
    ) as replace_snapshot:
        summary = run_daily_scrape(":memory:")

    assert summary["failed"] == 2
    assert summary["moneydj_success"] == 1
    assert {row["etf_code"] for row in summary["failures"]} == failed_codes
    assert replace_snapshot.call_count == 1


def test_run_daily_scrape_saves_only_canonical_holdings():
    with _patch_run_date(), _patch_active_etfs(), patch(
        "pipeline.scrape_holdings",
        side_effect=lambda code, target_date=None: make_success(code),
    ):
        run_daily_scrape(":memory:")

    with db._connect() as conn:
        holdings = conn.execute("SELECT COUNT(*) FROM etf_daily_holdings").fetchone()[0]
        scrape_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='etf_scrape_runs'"
        ).fetchone()
    assert holdings == 3
    assert scrape_table is None


def test_run_daily_scrape_uses_run_date_not_source_data_date():
    with _patch_run_date(NextRunDate), _patch_active_etfs(), patch(
        "pipeline.scrape_holdings",
        side_effect=lambda code, target_date=None: make_success(
            code, row_date="2026/06/22"
        ),
    ):
        summary = run_daily_scrape(":memory:")

    assert summary["date"] == "2026-06-23"
    assert summary["data_freshness"] == {"fresh": 0, "stale": 3, "unknown": 0}
    assert summary["data_date_min"] == "2026-06-22"
    assert summary["data_date_max"] == "2026-06-22"


def test_mixed_source_dates_are_preserved_in_summary_and_holdings():
    def fake_scrape(code, target_date=None):
        row_date = "2026/06/22" if code == "00980A" else "2026/06/23"
        return make_success(code, row_date=row_date)

    with _patch_run_date(NextRunDate), _patch_active_etfs(), patch(
        "pipeline.scrape_holdings", side_effect=fake_scrape
    ):
        summary = run_daily_scrape(":memory:")

    assert summary["data_freshness"] == {"fresh": 2, "stale": 1, "unknown": 0}
    assert summary["stale_etfs"][0]["etf_code"] == "00980A"
    assert summary["data_date_min"] == "2026-06-22"
    assert summary["data_date_max"] == "2026-06-23"
    with db._connect() as conn:
        dates = conn.execute(
            "SELECT DISTINCT date FROM etf_daily_holdings ORDER BY date"
        ).fetchall()
    assert dates == [("2026-06-22",), ("2026-06-23",)]


def test_unknown_source_date_is_rejected_without_stopping_later_etfs():
    unknown = make_success("00980A")
    unknown["stock_rows"][0]["date"] = ""

    def fake_scrape(code, target_date=None):
        return unknown if code == "00980A" else make_success(code)

    with _patch_run_date(), _patch_active_etfs(), patch(
        "pipeline.scrape_holdings", side_effect=fake_scrape
    ), patch("pipeline._check_moneydj_warning"):
        summary = run_daily_scrape(":memory:")

    assert summary["data_freshness"] == {"fresh": 2, "stale": 0, "unknown": 1}
    assert summary["unknown_date_etfs"][0]["etf_code"] == "00980A"
    with db._connect() as conn:
        codes = conn.execute(
            "SELECT DISTINCT etf_code FROM etf_daily_holdings ORDER BY etf_code"
        ).fetchall()
    assert codes == [("00981A",), ("00982A",)]
''',
)


# Snapshot-date validation no longer asserts an auxiliary persisted attempt row.
path = "tests/test_snapshot_date_validation.py"
text = read(path)
text = text.replace(
    '''        patch("pipeline.replace_daily_snapshot", return_value={"inserted": True}) as replace_snapshot, \\
        patch("pipeline.insert_scrape_run") as insert_scrape_run, \\
        patch("pipeline._check_moneydj_warning") as check_moneydj_warning:
''',
    '''        patch("pipeline.replace_daily_snapshot", return_value={"inserted": True}) as replace_snapshot, \\
        patch("pipeline._check_moneydj_warning") as check_moneydj_warning:
''',
)
text = text.replace(
    '''        replace_snapshot,
        insert_scrape_run,
        check_moneydj_warning,
''',
    '''        replace_snapshot,
        check_moneydj_warning,
''',
)
text = re.sub(
    r"\n\ndef assert_failed_run\(.*?\n\n(?=def test_missing_source_date)",
    "\n\n",
    text,
    flags=re.S,
)
text = text.replace("summary, _, replace_snapshot, insert_scrape_run, _", "summary, _, replace_snapshot, _")
text = text.replace("summary, scrape_holdings, replace_snapshot, insert_scrape_run, _", "summary, scrape_holdings, replace_snapshot, _")
text = text.replace("_, _, _, _, check_moneydj_warning", "_, _, _, check_moneydj_warning")
text = text.replace("summary, _, _, _, check_moneydj_warning", "summary, _, _, check_moneydj_warning")
text = re.sub(r"\n\s*assert_failed_run\([^\n]+\)", "", text)
text = re.sub(
    r"\n\s*scrape_run = insert_scrape_run\.call_args\.args\[0\]\n\s*assert scrape_run\.status == \"success\"\n\s*assert scrape_run\.data_date == RUN_DATE",
    "",
    text,
)
text = re.sub(
    r"\n\s*assert \[call\.args\[0\]\.status for call in insert_scrape_run\.call_args_list\] == \[\n\s*\"failed\",\n\s*\"success\",\n\s*\]",
    "",
    text,
)
write(path, text)


# Selected retry still validates explicit date and target writes, without attempt logs.
path = "tests/test_selected_pipeline_retry.py"
text = read(path)
text = re.sub(
    r", \\\n\s*patch\(\"pipeline\.insert_scrape_run\"\) as insert_scrape_run",
    "",
    text,
)
text = text.replace("    assert insert_scrape_run.call_count == 2\n", "")
text = text.replace(
    '''    assert insert_scrape_run.call_args.args[0].date == date(2026, 7, 6)
    assert insert_scrape_run.call_args.args[0].data_date == date(2026, 7, 6)
''',
    '''    assert scraper.await_args.args[2] == date(2026, 7, 6)
''',
)
write(path, text)


# Simple mocks of the removed writer are deleted across unaffected behavioral suites.
for path in [
    "tests/test_row_count_validation.py",
    "tests/test_expected_data_date_cutoff.py",
    "tests/test_tw_stock_trading_calendar.py",
    "tests/test_daily_snapshot_replacement.py",
    "tests/test_weight_validation_warnings.py",
    "tests/test_scraper_freshness_target.py",
    "tests/test_pipeline_isolation_regression.py",
]:
    text = read(path)
    text = text.replace("pipeline.successful_snapshot_exists", "pipeline.snapshot_exists")
    text = re.sub(
        r", \\\n\s*patch\(\"pipeline\.insert_scrape_run\"(?:,[^\n]*)?\)(?: as \w+)?",
        "",
        text,
    )
    text = text.replace('), patch("pipeline.insert_scrape_run"):', '):')
    text = text.replace(', patch("pipeline.insert_scrape_run"):', ':')
    text = re.sub(r"\n\s*insert_scrape_run\.assert_[^\n]+", "", text)
    write(path, text)


# Stale-existing behavior remains a summary/write decision only.
write(
    "tests/test_skip_stale_existing_snapshots.py",
    '''from datetime import date, datetime
from unittest.mock import patch

import db
import pipeline
from models import HoldingRow
from pipeline import run_daily_scrape


RUN_DATE = date(2026, 6, 23)
STALE_DATA_DATE = date(2026, 6, 22)
RUN_AT = datetime.combine(
    RUN_DATE,
    pipeline.DATA_AVAILABILITY_CUTOFF,
    tzinfo=pipeline.TAIPEI_TIMEZONE,
)
ETFS = [{"code": "00980A"}]


def make_success(row_date="2026/06/22"):
    row = {
        "date": row_date,
        "etf_code": "00980A",
        "asset_name": "台積電(2330.TW)",
        "asset_type": "stock",
        "stock_code": "2330",
        "stock_name": "台積電",
        "shares": 1000,
        "weight_pct": 10.0,
        "source_url": "https://example.test",
        "source_type": "moneydj_primary",
        "extraction_method": "requests_bs4",
    }
    return {
        "ok": True,
        "reason": "ok",
        "all_rows": [row],
        "stock_rows": [row],
        "non_stock_rows": [],
        "source_url": "https://example.test",
        "source_type": "moneydj_primary",
        "total_weight_all_rows": 10.0,
        "total_weight_stock_rows": 10.0,
    }


def _base_patches(result):
    return (
        patch("pipeline._current_run_at", return_value=RUN_AT),
        patch("pipeline.latest_tw_trading_day_on_or_before", return_value=RUN_DATE),
        patch("pipeline.is_tw_trading_day", return_value=True),
        patch("pipeline._active_etfs_for_run", return_value=ETFS),
        patch("pipeline.scrape_holdings", return_value=result),
        patch("pipeline.init_db"),
    )


def test_stale_result_with_existing_snapshot_skips_holding_replacement():
    patches = _base_patches(make_success())
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patch(
        "pipeline.snapshot_exists",
        side_effect=lambda data_date, _: data_date == STALE_DATA_DATE,
    ) as snapshot_exists, patch(
        "pipeline.replace_daily_snapshot"
    ) as replace_snapshot:
        summary = run_daily_scrape(":memory:")

    assert snapshot_exists.call_args_list[-1].args == (STALE_DATA_DATE, "00980A")
    replace_snapshot.assert_not_called()
    assert summary["skipped_stale_existing"] == 1
    assert summary["stale_existing_etfs"][0]["data_date"] == "2026-06-22"
    assert summary["data_freshness"] == {"fresh": 0, "stale": 1, "unknown": 0}


def test_stale_result_without_existing_snapshot_writes_once():
    patches = _base_patches(make_success())
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patch(
        "pipeline.snapshot_exists", return_value=False
    ), patch(
        "pipeline.replace_daily_snapshot", return_value={"inserted": True}
    ) as replace_snapshot:
        summary = run_daily_scrape(":memory:")

    replace_snapshot.assert_called_once()
    assert summary["skipped_stale_existing"] == 0
    assert summary["data_freshness"] == {"fresh": 0, "stale": 1, "unknown": 0}


def test_fresh_result_writes_target_snapshot():
    patches = _base_patches(make_success("2026/06/23"))
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patch(
        "pipeline.snapshot_exists", return_value=False
    ) as snapshot_exists, patch(
        "pipeline.replace_daily_snapshot", return_value={"inserted": True}
    ) as replace_snapshot:
        summary = run_daily_scrape(":memory:")

    assert snapshot_exists.call_count == 1  # preexisting target check only
    replace_snapshot.assert_called_once()
    assert summary["data_freshness"] == {"fresh": 1, "stale": 0, "unknown": 0}


def test_snapshot_exists_detects_existing_stock_snapshot():
    db.init_db(":memory:")
    assert db.snapshot_exists(STALE_DATA_DATE, "00980A") is False
    db.insert_holdings([
        HoldingRow(
            date=STALE_DATA_DATE,
            etf_code="00980A",
            asset_name="台積電(2330.TW)",
            asset_type="stock",
            stock_code="2330",
            stock_name="台積電",
            shares=1000,
            weight_pct=10.0,
            source_url="https://example.test",
            source_type="moneydj_primary",
            extraction_method="requests_bs4",
            scraped_at=datetime(2026, 6, 23, 19, 30),
        )
    ])
    assert db.snapshot_exists(STALE_DATA_DATE, "00980A") is True
''',
)


# Classification tests seed holdings/universe directly; auxiliary success rows are obsolete.
path = "tests/test_change_classification_version.py"
text = read(path)
text = between(
    text,
    "def _insert_scrape_success(date_value):\n",
    "def test_init_db_creates_classification_version_column():\n",
    "def test_init_db_creates_classification_version_column():\n",
    "remove classification scrape fixture",
)
text = text.replace('    _insert_scrape_success("2026-06-20")\n', '')
text = text.replace('    _insert_scrape_success("2026-06-23")\n', '')
write(path, text)


# Report redesign derives the missing ETF directly from holdings coverage.
path = "tests/test_report_redesign.py"
text = read(path)
text = between(
    text,
    "def insert_scrape_run(date, etf_code, status=\"success\"):\n",
    "def ensure_signal_table():\n",
    "def ensure_signal_table():\n",
    "remove report redesign scrape helper",
)
text = text.replace('        insert_scrape_run("2026-06-26", etf_code)\n', '')
text = text.replace('    insert_scrape_run("2026-06-26", "00400A", status="failed")\n', '')
text = text.replace(
    "def test_report_puts_data_quality_before_summary_and_shows_failed_etfs():",
    "def test_report_puts_data_quality_before_summary_and_shows_missing_etfs():",
)
write(path, text)


# Failed-attempt report sections no longer exist; change diagnostics coverage remains.
path = "tests/test_report_change_diagnostics.py"
text = read(path)
text = between(
    text,
    "def insert_failed_scrape_run(date, etf_code, error=\"test failure\"):\n",
    "def insert_change_diagnostic(\n",
    "def insert_change_diagnostic(\n",
    "remove failed scrape helper",
)
text = between(
    text,
    "def test_retired_etf_with_failed_scrape_run_does_not_appear_in_report_failed_section():\n",
    "def test_retired_etf_with_skipped_change_diagnostic_does_not_appear_in_report():\n",
    "def test_retired_etf_with_skipped_change_diagnostic_does_not_appear_in_report():\n",
    "remove failed scrape report tests",
)
write(path, text)


# Pre-listing quality is represented by candidate-date coverage and diagnostics filters.
path = "tests/test_prelisting_etfs.py"
text = read(path)
text = between(
    text,
    "def _insert_scrape_run(status, data_date=None):\n",
    "def test_init_db_owns_listing_date_column():\n",
    "def test_init_db_owns_listing_date_column():\n",
    "remove prelisting scrape helper",
)
text = text.replace('    _insert_scrape_run("failed")\n', '')
text = text.replace('    assert "00408A" not in quality["failed_etfs"]\n', '    assert "00408A" not in quality["missing_etfs"]\n')
start = text.find("def test_report_excludes_prelisting_freshness_and_change_diagnostics():\n")
end = text.find("def test_report_warnings_use_historical_universe_count():\n", start)
if start < 0 or end < 0:
    raise RuntimeError("prelisting quality test markers")
replacement = '''def test_report_excludes_prelisting_holdings_gap_and_change_diagnostics():
    db.init_db(":memory:")
    _seed_with_future_etf()
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_change_diagnostics (
                date, prev_date, etf_code, status, reason, created_at
            ) VALUES (?, ?, ?, 'skipped', 'test', ?)
            """,
            ("2026-07-14", "2026-07-13", "00408A", "2026-07-14T21:00:00"),
        )

    quality = report._get_data_quality("2026-07-14")
    assert "00408A" not in quality["missing_etfs"]
    assert report._get_skipped_change_diagnostics("2026-07-14") == []


'''
text = text[:start] + replacement + text[end:]
write(path, text)


# Retired exclusion test now covers holdings gaps rather than failed attempt rows.
path = "tests/test_signal_report_retired_exclusion.py"
text = read(path)
text = between(
    text,
    "def _seed_scrape_run(conn, date, etf_code, status):\n",
    "def _seed_change_diagnostic(\n",
    "def _seed_change_diagnostic(\n",
    "remove retired scrape helper",
)
start = text.find("def test_failed_etfs_excludes_retired_etfs():\n")
end = text.find("def test_change_skips_exclude_retired_etfs():\n", start)
if start < 0 or end < 0:
    raise RuntimeError("retired quality test markers")
replacement = '''def test_missing_target_holdings_excludes_retired_etfs():
    db.init_db(":memory:")
    with db._connect() as conn:
        _seed_universe(conn, [
            ("00983A", "CTBC", 1),
            ("00980A", "Nomura", 0),
        ])

    report_text = generate_signal_report("2026-06-23")
    assert "00980A" in report_text
    assert "00983A" not in report_text


'''
write(path, text[:start] + replacement + text[end:])

print("Applied holdings-source full-suite cleanup")
