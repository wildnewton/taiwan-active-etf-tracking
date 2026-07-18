from datetime import date, datetime
from unittest.mock import patch

import db
import pipeline
from manager_intent import generate_manager_intent_rollups


TARGET_DATE = "2026-07-17"
PREVIOUS_DATE = "2026-07-16"
LISTING_DATE = "2026-07-20"


def _seed_etf(code: str, issuer: str) -> None:
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_universe (
                code, name, issuer, listing_date, retired,
                first_seen_date, created_at, updated_at
            ) VALUES (?, ?, ?, '2026-07-01', 0, '2026-07-01', ?, ?)
            """,
            (
                code,
                code,
                issuer,
                "2026-07-01T00:00:00",
                "2026-07-01T00:00:00",
            ),
        )


def _insert_stock(
    data_date: str,
    etf_code: str,
    stock_code: str,
    weight_pct: float,
    source_type: str,
) -> None:
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_daily_holdings (
                date, etf_code, asset_name, asset_type, stock_code, stock_name,
                shares, weight_pct, source_url, source_type,
                extraction_method, scraped_at
            ) VALUES (?, ?, ?, 'stock', ?, ?, 100, ?,
                      'https://example.test', ?, 'test', ?)
            """,
            (
                data_date,
                etf_code,
                f"Stock {stock_code}",
                stock_code,
                f"Stock {stock_code}",
                weight_pct,
                source_type,
                f"{data_date}T21:00:00",
            ),
        )


def test_daily_scrape_selects_default_universe_for_target_holdings_date():
    run_at = datetime(
        2026,
        7,
        20,
        14,
        0,
        tzinfo=pipeline.TAIPEI_TIMEZONE,
    )
    target_date = date.fromisoformat(TARGET_DATE)
    listing_date = date.fromisoformat(LISTING_DATE)

    def active_etfs(as_of_date):
        return [{"code": "NEW"}] if as_of_date >= listing_date else []

    with patch("pipeline.init_db"), patch(
        "pipeline._current_run_at",
        return_value=run_at,
    ), patch(
        "pipeline._expected_data_date_for_run",
        return_value=target_date,
    ), patch(
        "pipeline._is_trading_day_for_run",
        return_value=True,
    ), patch(
        "pipeline._active_etfs_for_run",
        side_effect=active_etfs,
    ) as active, patch(
        "pipeline.snapshot_exists",
        return_value=False,
    ), patch(
        "pipeline.scrape_holdings",
    ) as scrape:
        summary = pipeline.run_daily_scrape(":memory:")

    active.assert_called_once_with(target_date)
    scrape.assert_not_called()
    assert summary["date"] == LISTING_DATE
    assert summary["expected_data_date"] == TARGET_DATE
    assert summary["total_etfs"] == 0


def test_manager_intent_uses_only_canonical_complete_holdings_rows():
    db.init_db(":memory:")
    _seed_etf("A", "IssuerA")
    _seed_etf("B", "IssuerB")

    # A has one complete canonical source and a conflicting incomplete source.
    _insert_stock(TARGET_DATE, "A", "2330", 100.0, "moneydj_primary")
    _insert_stock(TARGET_DATE, "A", "2454", 10.0, "official_fallback")

    # B has only an incomplete snapshot on a date made usable by A.
    _insert_stock(TARGET_DATE, "B", "3711", 10.0, "moneydj_primary")

    generate_manager_intent_rollups(TARGET_DATE, windows=(5,))

    with db._connect() as conn:
        rows = conn.execute(
            """
            SELECT issuer_key, stock_code
            FROM manager_intent_rollups
            WHERE date = ? AND window_days = 5
              AND entity_level = 'issuer_stock'
            ORDER BY issuer_key, stock_code
            """,
            (TARGET_DATE,),
        ).fetchall()

    assert rows == [("IssuerA", "2330")]


def test_manager_intent_fallback_context_excludes_incomplete_etf_dates():
    db.init_db(":memory:")
    _seed_etf("A", "IssuerA")
    _seed_etf("B", "IssuerB")

    # A makes the older date usable, while B has only an incomplete row there.
    _insert_stock(PREVIOUS_DATE, "A", "2330", 100.0, "moneydj_primary")
    _insert_stock(PREVIOUS_DATE, "B", "3711", 10.0, "moneydj_primary")

    # B becomes a valid candidate on the newer date through a complete snapshot.
    _insert_stock(TARGET_DATE, "B", "3711", 100.0, "moneydj_primary")

    generate_manager_intent_rollups(TARGET_DATE, windows=(5,))

    with db._connect() as conn:
        row = conn.execute(
            """
            SELECT eligible_days
            FROM manager_intent_rollups
            WHERE date = ? AND window_days = 5
              AND entity_level = 'issuer_stock'
              AND issuer_key = 'IssuerB' AND stock_code = '3711'
            """,
            (TARGET_DATE,),
        ).fetchone()

    assert row == (1,)
