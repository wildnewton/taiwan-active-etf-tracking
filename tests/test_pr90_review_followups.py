from datetime import datetime

import db
import report
from changes import detect_holding_changes
from etf_universe import get_eligible_etf_codes, get_etf_config, reconcile_discovered_universe
from manager_intent import generate_manager_intent_rollups


D1 = "2026-07-01"
D2 = "2026-07-02"
D3 = "2026-07-03"
D4 = "2026-07-04"
D5 = "2026-07-05"
D6 = "2026-07-06"


def _seed_etf(
    code,
    *,
    issuer="TestIssuer",
    listing_date=D1,
    retired=0,
    official_logic=None,
):
    now = datetime.now().isoformat()
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_universe (
                code, name, issuer, listing_date, retired, first_seen_date,
                official_logic, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                code,
                code,
                issuer,
                listing_date,
                retired,
                listing_date,
                official_logic,
                now,
                now,
            ),
        )


def _insert_stock(
    data_date,
    etf_code,
    *,
    stock_code="2330",
    weight=100.0,
    shares=100.0,
    source_type="moneydj_primary",
):
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_daily_holdings (
                date, etf_code, asset_name, asset_type, stock_code, stock_name,
                shares, weight_pct, source_url, source_type,
                extraction_method, scraped_at
            ) VALUES (?, ?, ?, 'stock', ?, ?, ?, ?, 'https://example.test',
                      ?, 'test', ?)
            """,
            (
                data_date,
                etf_code,
                f"Stock {stock_code}",
                stock_code,
                f"Stock {stock_code}",
                shares,
                weight,
                source_type,
                f"{data_date}T21:00:00",
            ),
        )


def _insert_non_stock(
    data_date,
    etf_code,
    *,
    weight,
    source_type="moneydj_primary",
    asset_name="Cash",
):
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_daily_non_stock_assets (
                date, etf_code, asset_name, asset_type, weight_pct,
                source_url, source_type, extraction_method, scraped_at
            ) VALUES (?, ?, ?, 'cash', ?, 'https://example.test', ?, 'test', ?)
            """,
            (
                data_date,
                etf_code,
                asset_name,
                weight,
                source_type,
                f"{data_date}T21:00:00",
            ),
        )


def _insert_complete_snapshot(
    data_date,
    etf_code,
    *,
    stock_weight=100.0,
    stock_code="2330",
    source_type="moneydj_primary",
    shares=100.0,
):
    _insert_stock(
        data_date,
        etf_code,
        stock_code=stock_code,
        weight=stock_weight,
        shares=shares,
        source_type=source_type,
    )
    if stock_weight < 100.0:
        _insert_non_stock(
            data_date,
            etf_code,
            weight=100.0 - stock_weight,
            source_type=source_type,
        )


def test_one_canonical_source_prefers_complete_snapshot_across_all_consumers():
    db.init_db(":memory:")
    _seed_etf("A")
    _insert_stock(
        D6,
        "A",
        stock_code="2330",
        weight=99.0,
        source_type="moneydj_primary",
    )
    _insert_complete_snapshot(
        D6,
        "A",
        stock_weight=50.0,
        stock_code="2454",
        source_type="official_fallback",
    )

    assert db.get_canonical_snapshot_source(D6, "A") == "official_fallback"

    from changes import _select_canonical_sources

    assert _select_canonical_sources(D6)["A"]["source_type"] == "official_fallback"
    assert [row["stock_code"] for row in report._canonical_stock_rows(D6)] == ["2454"]


def test_ineligible_only_date_does_not_create_false_consecutive_add():
    db.init_db(":memory:")
    _seed_etf("A")
    _seed_etf("FUTURE", listing_date="2026-07-20")
    _insert_complete_snapshot(D1, "A", stock_weight=2.0, shares=100.0)
    _insert_complete_snapshot(D2, "FUTURE")
    _insert_complete_snapshot(D3, "A", stock_weight=2.0, shares=100.0)

    summary = detect_holding_changes(D3, D1)

    assert summary["ok"] is True
    with db._connect() as conn:
        row = conn.execute(
            """
            SELECT consecutive_add_days, consecutive_reduce_days
            FROM etf_holding_changes
            WHERE date = ? AND etf_code = 'A' AND stock_code = '2330'
            """,
            (D3,),
        ).fetchone()
    assert row == (0, 0)


def test_ineligible_only_date_does_not_consume_manager_intent_window():
    db.init_db(":memory:")
    _seed_etf("A", issuer="IssuerA")
    _seed_etf("FUTURE", listing_date="2026-07-20")
    for data_date in (D1, D2, D3, D4, D6):
        _insert_complete_snapshot(data_date, "A", stock_weight=5.0)
    _insert_complete_snapshot(D5, "FUTURE")

    generate_manager_intent_rollups(D6, windows=(5,))

    with db._connect() as conn:
        row = conn.execute(
            """
            SELECT eligible_days
            FROM manager_intent_rollups
            WHERE date = ? AND window_days = 5
              AND entity_level = 'issuer_stock'
              AND stock_code = '2330' AND issuer_key = 'IssuerA'
            """,
            (D6,),
        ).fetchone()
    assert row == (5,)


def test_report_counts_canonical_non_stock_assets():
    db.init_db(":memory:")
    _seed_etf("A")
    _insert_complete_snapshot(D6, "A", stock_weight=90.0)

    assert report._get_summary_stats(D6) == {
        "etf_count": 1,
        "stock_count": 1,
        "non_stock_count": 1,
    }


def test_discovery_replay_does_not_change_manual_retirement_or_holdings_cutoff():
    db.init_db(":memory:")
    _seed_etf("A", retired=1)
    _insert_complete_snapshot(D6, "A")

    reconcile_discovered_universe(
        [{"code": "A", "name": "A", "listing_date": D1}],
        seen_date="2026-07-10",
    )

    assert get_etf_config("A")["retired"] == 1
    assert "A" in get_eligible_etf_codes(D6)
    assert "A" not in get_eligible_etf_codes("2026-07-10")


def test_latest_available_snapshot_respects_snapshot_date_eligibility():
    db.init_db(":memory:")
    _seed_etf("A", listing_date="2026-07-15")
    _insert_complete_snapshot(D1, "A")

    coverage = db.get_target_snapshot_coverage("2026-07-16")

    assert coverage["missing_etfs"] == ["A"]
    assert coverage["latest_available_dates"] == {"A": None}
