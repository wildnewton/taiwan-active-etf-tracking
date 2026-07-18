from datetime import datetime

import db
from etf_universe import (
    get_eligible_etf_codes,
    get_etf_config,
    reconcile_discovered_universe,
    upsert_etf,
)


OLDER_DATE = "2026-07-16"
LATEST_DATE = "2026-07-17"
DISCOVERY_DATE = "2026-07-18"


def _seed_etf(
    code: str,
    *,
    retired: int = 0,
    listing_date: str = "2026-07-01",
) -> None:
    upsert_etf(
        {
            "code": code,
            "name": code,
            "issuer": f"Issuer-{code}",
            "listing_date": listing_date,
            "retired": retired,
        }
    )


def _insert_stock_holding(data_date: str, etf_code: str, weight_pct: float = 1.0) -> None:
    now = datetime.now().isoformat()
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_daily_holdings (
                date, etf_code, asset_name, asset_type, stock_code, stock_name,
                shares, weight_pct, source_url, source_type,
                extraction_method, scraped_at
            ) VALUES (?, ?, '台積電', 'stock', '2330', '台積電', 1, ?,
                      'https://example.test', 'test', 'test', ?)
            """,
            (data_date, etf_code, weight_pct, now),
        )


def _insert_non_stock_holding(
    data_date: str,
    etf_code: str,
    weight_pct: float = 1.0,
) -> None:
    now = datetime.now().isoformat()
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_daily_non_stock_assets (
                date, etf_code, asset_name, asset_type, weight_pct,
                source_url, source_type, extraction_method, scraped_at
            ) VALUES (?, ?, '現金', 'cash', ?, 'https://example.test',
                      'test', 'test', ?)
            """,
            (data_date, etf_code, weight_pct, now),
        )


def _seed_two_usable_dates() -> None:
    _seed_etf("REFERENCE")
    _insert_stock_holding(OLDER_DATE, "REFERENCE")
    _insert_stock_holding(LATEST_DATE, "REFERENCE")


def _discovered(*codes: str) -> list[dict]:
    return [
        {
            "code": code,
            "name": code,
            "listing_date": "2026-07-01",
        }
        for code in codes
    ]


def test_complete_discovery_reports_candidate_without_changing_retired_state():
    db.init_db(":memory:")
    _seed_etf("MISSING")
    _seed_two_usable_dates()

    summary = reconcile_discovered_universe(
        _discovered("REFERENCE"),
        seen_date=DISCOVERY_DATE,
        discovery_complete=True,
    )

    assert summary["retirement_candidates"] == ["MISSING"]
    assert get_etf_config("MISSING")["retired"] == 0


def test_stock_holding_on_either_recent_date_prevents_candidate_status():
    db.init_db(":memory:")
    _seed_etf("PRESENT")
    _seed_two_usable_dates()
    _insert_stock_holding(LATEST_DATE, "PRESENT")

    summary = reconcile_discovered_universe(
        _discovered("REFERENCE"),
        seen_date=DISCOVERY_DATE,
        discovery_complete=True,
    )

    assert "PRESENT" not in summary["retirement_candidates"]


def test_non_stock_holding_on_either_recent_date_prevents_candidate_status():
    db.init_db(":memory:")
    _seed_etf("PRESENT")
    _seed_two_usable_dates()
    _insert_non_stock_holding(OLDER_DATE, "PRESENT")

    summary = reconcile_discovered_universe(
        _discovered("REFERENCE"),
        seen_date=DISCOVERY_DATE,
        discovery_complete=True,
    )

    assert "PRESENT" not in summary["retirement_candidates"]


def test_fewer_than_two_usable_holdings_dates_produces_no_candidate():
    db.init_db(":memory:")
    _seed_etf("MISSING")
    _seed_etf("REFERENCE")
    _insert_stock_holding(LATEST_DATE, "REFERENCE")

    summary = reconcile_discovered_universe(
        _discovered("REFERENCE"),
        seen_date=DISCOVERY_DATE,
        discovery_complete=True,
    )

    assert summary["retirement_candidates"] == []
    assert get_etf_config("MISSING")["retired"] == 0


def test_recently_listed_etf_is_not_a_retirement_candidate():
    db.init_db(":memory:")
    _seed_etf("NEW", listing_date=LATEST_DATE)
    _seed_two_usable_dates()

    summary = reconcile_discovered_universe(
        _discovered("REFERENCE"),
        seen_date=DISCOVERY_DATE,
        discovery_complete=True,
    )

    assert "NEW" not in summary["retirement_candidates"]


def test_incomplete_discovery_produces_no_candidate_or_retirement_change():
    db.init_db(":memory:")
    _seed_etf("MISSING")
    _seed_two_usable_dates()

    summary = reconcile_discovered_universe(
        _discovered("REFERENCE"),
        seen_date=DISCOVERY_DATE,
        discovery_complete=False,
    )

    assert summary["retirement_candidates"] == []
    assert get_etf_config("MISSING")["retired"] == 0


def test_discovery_does_not_reactivate_a_manually_retired_etf():
    db.init_db(":memory:")
    _seed_etf("RETIRED", retired=1)

    summary = reconcile_discovered_universe(
        _discovered("RETIRED"),
        seen_date=DISCOVERY_DATE,
        discovery_complete=True,
    )

    assert summary["reactivated"] == []
    assert get_etf_config("RETIRED")["retired"] == 1


def test_legacy_lifecycle_columns_do_not_control_historical_eligibility():
    db.init_db(":memory:")
    _seed_etf("HISTORICAL", retired=1)
    _insert_stock_holding(OLDER_DATE, "HISTORICAL")
    with db._connect() as conn:
        conn.execute(
            """
            UPDATE etf_universe
            SET last_active_date = ?, pending_retirement_since = ?
            WHERE code = 'HISTORICAL'
            """,
            (DISCOVERY_DATE, OLDER_DATE),
        )

    assert "HISTORICAL" not in get_eligible_etf_codes(LATEST_DATE)


def test_retired_historical_cutoff_is_derived_from_latest_holdings_date():
    db.init_db(":memory:")
    _seed_etf("HISTORICAL", retired=1)
    _insert_stock_holding(OLDER_DATE, "HISTORICAL")
    _insert_non_stock_holding(LATEST_DATE, "HISTORICAL")

    assert "HISTORICAL" in get_eligible_etf_codes(LATEST_DATE)
    assert "HISTORICAL" not in get_eligible_etf_codes(DISCOVERY_DATE)
