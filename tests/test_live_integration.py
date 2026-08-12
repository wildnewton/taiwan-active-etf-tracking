import os
from datetime import date

import pytest

import db
import etf_universe
from scraper import scrape_holdings
from snapshot_validation import validate_snapshot_rows


pytestmark = pytest.mark.live


def pytest_generate_tests(metafunc):
    if "active_etf" not in metafunc.fixturenames:
        return

    raw_date = os.getenv("ETF_LIVE_DATE")
    try:
        target_date = date.fromisoformat(raw_date) if raw_date else None
    except ValueError:
        target_date = None

    active_etfs = etf_universe.get_active_etfs(target_date) if target_date else [None]
    if not active_etfs:
        active_etfs = [None]

    metafunc.parametrize(
        "active_etf",
        active_etfs,
        ids=lambda etf: etf["code"] if etf else "no-active-etf",
    )


@pytest.fixture(scope="session")
def etf_live_date() -> date:
    raw_date = os.getenv("ETF_LIVE_DATE")
    if not raw_date:
        pytest.skip("set ETF_LIVE_DATE=YYYY-MM-DD to run live integration tests")
    try:
        return date.fromisoformat(raw_date)
    except ValueError:
        pytest.fail("ETF_LIVE_DATE must use YYYY-MM-DD format")


def _operational_row_counts() -> tuple[int, int]:
    conn = db._connect()
    try:
        stock_count = conn.execute(
            "SELECT COUNT(*) FROM etf_daily_holdings"
        ).fetchone()[0]
        non_stock_count = conn.execute(
            "SELECT COUNT(*) FROM etf_daily_non_stock_assets"
        ).fetchone()[0]
        return stock_count, non_stock_count
    finally:
        if db._DB_PATH != ":memory:":
            conn.close()


@pytest.fixture(scope="session", autouse=True)
def preserve_operational_holdings_counts(etf_live_date: date):
    counts_before = _operational_row_counts()
    yield
    counts_after = _operational_row_counts()
    assert counts_after == counts_before, (
        "live integration tests modified operational holdings row counts: "
        f"before={counts_before}, after={counts_after}"
    )


def test_production_scraper_returns_valid_snapshot_for_active_etf(
    active_etf: dict | None,
    etf_live_date: date,
):
    assert active_etf is not None, (
        f"no active ETFs found in operational configuration for {etf_live_date}"
    )
    etf_code = active_etf["code"]
    result = scrape_holdings(etf_code, etf_live_date)
    valid, validation_reason = validate_snapshot_rows(result["all_rows"])

    print(
        f"etf_code={etf_code} "
        f"source_type={result.get('source_type', '')} "
        f"source_url={result.get('source_url', '')} "
        f"stock_rows={len(result.get('stock_rows') or [])} "
        f"date={etf_live_date.strftime('%Y/%m/%d')} "
        f"validation={'ok' if valid else validation_reason}"
    )

    failure_context = (
        f"{etf_code} ({active_etf.get('issuer')}) "
        f"method={active_etf.get('official_method')} "
        f"source_type={result.get('source_type', '')} "
        f"reason={result.get('reason', 'unknown')}"
    )
    assert result["ok"] is True, failure_context
    assert result["stock_rows"], f"{etf_code}: no stock rows"
    assert result["source_type"], f"{etf_code}: missing source_type"
    assert result["source_url"], f"{etf_code}: missing source_url"
    assert all(
        row["etf_code"] == etf_code for row in result["stock_rows"]
    ), f"{etf_code}: stock rows contain a different ETF identity"
    assert all(
        row["date"] == etf_live_date.strftime("%Y/%m/%d")
        for row in result["stock_rows"]
    ), f"{etf_code}: stock rows do not match requested date {etf_live_date}"
    assert valid, f"{etf_code}: snapshot validation failed: {validation_reason}"
