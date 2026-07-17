from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def write(path, text):
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


# The old audit suite was entirely about the removed hybrid scrape-run state.
(ROOT / "tests/test_date_semantics_audit.py").unlink()


write(
    "tests/test_date_semantics_final_review.py",
    '''from unittest.mock import patch

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
    last_active_date: str | None = None,
) -> None:
    with db._connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO etf_universe (
                code, name, listing_date, retired, last_active_date,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                code,
                code,
                listing_date,
                retired,
                last_active_date,
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
    _seed_etf("RETIRED", retired=1, last_active_date="2026-07-14")

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
''',
)


path = "tests/test_models.py"
text = read(path)
text = text.replace(
    "from models import HoldingRow, NonStockAssetRow, ScrapeResult, ScrapeRun\n",
    "from models import HoldingRow, NonStockAssetRow, ScrapeResult\n",
)
marker = "\n\ndef test_scrape_run_is_dataclass_with_required_fields():\n"
index = text.find(marker)
if index < 0:
    raise RuntimeError("models scrape-run tests marker not found")
write(path, text[:index].rstrip() + "\n")


write(
    "tests/test_preexisting_successful_snapshots.py",
    '''from datetime import date, datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest

import db
import pipeline
from models import HoldingRow


RUN_DATE = date(2026, 7, 14)
RUN_AT = datetime(
    2026,
    7,
    14,
    15,
    0,
    tzinfo=pipeline.TAIPEI_TIMEZONE,
)


def _holding(etf_code: str, data_date: date = RUN_DATE) -> HoldingRow:
    return HoldingRow(
        date=data_date,
        etf_code=etf_code,
        asset_name="台積電(2330.TW)",
        asset_type="stock",
        stock_code="2330",
        stock_name="台積電",
        shares=1000,
        weight_pct=10.0,
        source_url="https://example.test",
        source_type="moneydj_primary",
        extraction_method="test",
        scraped_at=datetime(2026, 7, 14, 15, 0),
    )


def _make_success(etf_code: str, data_date: date = RUN_DATE) -> dict:
    row = {
        "date": data_date.isoformat(),
        "etf_code": etf_code,
        "asset_name": "台積電(2330.TW)",
        "asset_type": "stock",
        "stock_code": "2330",
        "stock_name": "台積電",
        "shares": 1000,
        "weight_pct": 10.0,
        "source_url": "https://example.test",
        "source_type": "moneydj_primary",
        "extraction_method": "test",
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


def _seed_snapshot(db_path, etf_code: str) -> None:
    db.init_db(str(db_path))
    db.insert_holdings([_holding(etf_code)])


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeBrowserStack:
    def __init__(self):
        self.page = Mock()
        self.page.close = AsyncMock()
        self.context = Mock()
        self.context.new_page = AsyncMock(return_value=self.page)
        self.context.close = AsyncMock()
        self.browser = Mock()
        self.browser.new_context = AsyncMock(return_value=self.context)
        self.browser.close = AsyncMock()
        self.playwright = Mock()
        self.playwright.chromium.launch = AsyncMock(return_value=self.browser)
        self.async_playwright = Mock(return_value=_AsyncContext(self.playwright))


def test_exact_snapshot_skips_without_auxiliary_status(tmp_path):
    db_path = tmp_path / "validated.sqlite"
    _seed_snapshot(db_path, "00980A")
    scraper = Mock(side_effect=AssertionError("scraper must not run"))

    summary = pipeline._run_scrape_sync(
        str(db_path),
        [{"code": "00980A"}],
        scraper,
        already_initialized=True,
        use_trading_calendar=False,
        run_at=RUN_AT,
    )

    scraper.assert_not_called()
    assert summary["preexisting_success"] == 1
    assert summary["moneydj_success"] == 0
    assert summary["failed"] == 0
    assert summary["data_freshness"] == {"fresh": 1, "stale": 0, "unknown": 0}
    assert summary["data_date_min"] == RUN_DATE.isoformat()
    assert summary["data_date_max"] == RUN_DATE.isoformat()


@pytest.mark.asyncio
async def test_all_complete_daily_browser_run_returns_before_playwright(tmp_path):
    db_path = tmp_path / "all-complete.sqlite"
    _seed_snapshot(db_path, "00980A")

    with patch("pipeline._current_run_at", return_value=RUN_AT), patch(
        "pipeline._active_etfs_for_run", return_value=[{"code": "00980A"}]
    ), patch(
        "pipeline.latest_tw_trading_day_on_or_before", return_value=RUN_DATE
    ), patch(
        "pipeline.is_tw_trading_day", return_value=True
    ), patch(
        "playwright.async_api.async_playwright",
        side_effect=AssertionError("Playwright must not start"),
    ) as async_playwright:
        summary = await pipeline.run_daily_scrape_with_browser_async(str(db_path))

    async_playwright.assert_not_called()
    assert summary["preexisting_success"] == 1
    assert summary["data_freshness"]["fresh"] == 1


@pytest.mark.asyncio
async def test_mixed_daily_browser_run_scrapes_only_missing_etfs(tmp_path):
    db_path = tmp_path / "mixed.sqlite"
    _seed_snapshot(db_path, "00980A")
    browser_stack = _FakeBrowserStack()
    scraper = AsyncMock(
        side_effect=lambda etf_code, page, target_date: _make_success(
            etf_code, target_date
        )
    )

    with patch("pipeline._current_run_at", return_value=RUN_AT), patch(
        "pipeline._active_etfs_for_run",
        return_value=[{"code": "00980A"}, {"code": "00981A"}],
    ), patch(
        "pipeline.latest_tw_trading_day_on_or_before", return_value=RUN_DATE
    ), patch(
        "pipeline.is_tw_trading_day", return_value=True
    ), patch(
        "playwright.async_api.async_playwright",
        new=browser_stack.async_playwright,
    ), patch(
        "pipeline.scrape_holdings_with_browser_async",
        new=scraper,
    ):
        summary = await pipeline.run_daily_scrape_with_browser_async(str(db_path))

    scraper.assert_awaited_once_with(
        "00981A",
        browser_stack.page,
        target_date=RUN_DATE,
    )
    assert summary["total_etfs"] == 2
    assert summary["preexisting_success"] == 1
    assert summary["moneydj_success"] == 1
    assert summary["failed"] == 0
    assert summary["data_freshness"] == {"fresh": 2, "stale": 0, "unknown": 0}


@pytest.mark.asyncio
async def test_selected_internal_browser_still_forces_scrape(tmp_path):
    db_path = tmp_path / "selected.sqlite"
    _seed_snapshot(db_path, "00980A")
    browser_stack = _FakeBrowserStack()
    scraper = AsyncMock(return_value=_make_success("00980A"))

    with patch(
        "playwright.async_api.async_playwright",
        new=browser_stack.async_playwright,
    ), patch(
        "pipeline.scrape_holdings_with_browser_async",
        new=scraper,
    ):
        summary = await pipeline.run_selected_scrape_with_browser_async(
            str(db_path),
            ["00980A"],
            run_date=RUN_DATE,
        )

    scraper.assert_awaited_once_with(
        "00980A",
        browser_stack.page,
        target_date=RUN_DATE,
    )
    assert summary["preexisting_success"] == 0
    assert summary["moneydj_success"] == 1
''',
)


path = "tests/test_try_run_preexisting_success.py"
text = read(path)
text = text.replace(
    "from models import HoldingRow, ScrapeRun\n",
    "from models import HoldingRow\n",
)
start = text.find("    db.insert_scrape_run(\n")
end = text.find("\n\n\ndef test_complete_try_run", start)
if start < 0 or end < 0:
    raise RuntimeError("try-run scrape seed block not found")
write(path, text[:start] + text[end:])


path = "tests/test_pipeline_isolation_regression.py"
text = read(path)
text = text.replace(
    "from models import HoldingRow, ScrapeRun\n",
    "from models import HoldingRow\n",
)
start = text.find("    db.insert_scrape_run(\n")
end = text.find("\n\n\ndef _success_result", start)
if start < 0 or end < 0:
    raise RuntimeError("isolation scrape seed block not found")
text = text[:start] + text[end:]
text = text.replace(
    ''') , patch("pipeline.insert_scrape_run"):
'''.replace(" )", ")"),
    "):\n",
)
text = text.replace(
    '''    ), patch("pipeline.replace_daily_snapshot", return_value={"inserted": True}
    ), patch("pipeline.insert_scrape_run"):
''',
    '''    ), patch("pipeline.replace_daily_snapshot", return_value={"inserted": True}
    ):
''',
)
write(path, text)

print("Applied holdings-source collection cleanup")
