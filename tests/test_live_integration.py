from datetime import date

import pytest

from scripts import db
from scripts.etf_universe import get_eligible_etf_codes, get_etf_config
from scripts.scrapers.moneydj import scrape_moneydj
from scripts.scrapers.official import get_official_config, scrape_official_static
from scripts.snapshot_validation import validate_snapshot_rows


pytestmark = pytest.mark.live

_EXPECTED_SOURCE_TYPES = {
    "moneydj": "moneydj_primary",
    "official": "official_fallback",
}


def _parse_live_date(raw_date: str) -> date:
    try:
        return date.fromisoformat(raw_date)
    except ValueError as exc:
        raise pytest.UsageError("--live-date must use YYYY-MM-DD format") from exc


def _diagnostic(
    etf_code: str,
    issuer: str | None,
    source: str,
    method: str | None,
    reason: str,
) -> str:
    return (
        f"ETF={etf_code}, issuer={issuer or 'unknown'}, source={source}, "
        f"method={method or ('n/a' if source == 'moneydj' else 'unknown')}, "
        f"reason={reason}"
    )


def _selected_sources(config: pytest.Config) -> tuple[str, ...]:
    selected = config.getoption("--live-source")
    if selected == "both":
        return ("moneydj", "official")
    return (selected,)


def pytest_generate_tests(metafunc):
    if not {"etf_code", "source"}.issubset(metafunc.fixturenames):
        return

    raw_date = metafunc.config.getoption("--live-date")
    if raw_date is None:
        metafunc.parametrize(
            ("etf_code", "source"),
            [
                pytest.param(
                    None,
                    None,
                    marks=pytest.mark.skip(
                        reason="live integration suite requires --live-date YYYY-MM-DD"
                    ),
                    id="live-date-required",
                )
            ],
        )
        return

    live_date = _parse_live_date(raw_date)
    cases = [
        pytest.param(etf_code, source, id=f"{etf_code}-{source}")
        for etf_code in get_eligible_etf_codes(live_date)
        for source in _selected_sources(metafunc.config)
    ]
    metafunc.parametrize(("etf_code", "source"), cases)


@pytest.fixture(scope="session")
def live_date(pytestconfig) -> date:
    raw_date = pytestconfig.getoption("--live-date")
    if raw_date is None:
        pytest.skip("live integration suite requires --live-date YYYY-MM-DD")
    return _parse_live_date(raw_date)


def _snapshot_table_counts() -> dict[str, int]:
    with db._connect() as conn:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "etf_daily_holdings",
                "etf_daily_non_stock_assets",
            )
        }


@pytest.fixture(scope="session", autouse=True)
def operational_db_is_unchanged(pytestconfig):
    before = _snapshot_table_counts()
    yield
    after = _snapshot_table_counts()
    source = pytestconfig.getoption("--live-source")
    method = "n/a" if source == "moneydj" else "various"
    assert after == before, _diagnostic(
        "all eligible ETFs",
        "multiple",
        source,
        method,
        f"operational DB row counts changed: before={before}, after={after}",
    )


def _row_date(value) -> date | None:
    if isinstance(value, date):
        return value
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value).replace("/", "-"))
    except ValueError:
        return None


def test_live_scraper_returns_requested_valid_snapshot(
    etf_code: str,
    source: str,
    live_date: date,
):
    if source == "official":
        try:
            config = get_official_config(etf_code)
        except KeyError:
            pytest.fail(
                _diagnostic(
                    etf_code,
                    "unknown",
                    source,
                    "unknown",
                    "missing official config",
                ),
                pytrace=False,
            )
        issuer = config.get("issuer")
        method = config.get("method")
        result = scrape_official_static(etf_code)
    else:
        try:
            config = get_etf_config(etf_code)
        except KeyError:
            pytest.fail(
                _diagnostic(
                    etf_code,
                    "unknown",
                    source,
                    None,
                    "missing ETF config",
                ),
                pytrace=False,
            )
        issuer = config.get("issuer")
        method = None
        result = scrape_moneydj(etf_code)

    reason = result.get("reason") or "scraper reported failure without a reason"
    assert result.get("ok") is True, _diagnostic(
        etf_code, issuer, source, method, reason
    )

    stock_rows = result.get("stock_rows") or []
    assert stock_rows, _diagnostic(
        etf_code, issuer, source, method, "scraper returned no stock rows"
    )

    expected_source_type = _EXPECTED_SOURCE_TYPES[source]
    actual_source_type = result.get("source_type")
    assert actual_source_type == expected_source_type, _diagnostic(
        etf_code,
        issuer,
        source,
        method,
        f"unexpected result source_type: expected={expected_source_type}, "
        f"actual={actual_source_type}",
    )

    wrong_codes = [
        row.get("etf_code")
        for row in stock_rows
        if row.get("etf_code") != etf_code
    ]
    assert not wrong_codes, _diagnostic(
        etf_code,
        issuer,
        source,
        method,
        f"stock rows contain mismatched ETF codes: {wrong_codes}",
    )

    wrong_dates = [
        row.get("date")
        for row in stock_rows
        if _row_date(row.get("date")) != live_date
    ]
    assert not wrong_dates, _diagnostic(
        etf_code,
        issuer,
        source,
        method,
        f"stock rows do not match requested live_date={live_date.isoformat()}: "
        f"actual={wrong_dates}",
    )

    valid, validation_reason = validate_snapshot_rows(result.get("all_rows") or [])
    assert valid is True, _diagnostic(
        etf_code,
        issuer,
        source,
        method,
        f"snapshot validation failed: {validation_reason}",
    )
