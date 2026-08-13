import sqlite3

import pytest

import test_live_integration as live


def test_live_collection_uses_production_active_universe(monkeypatch):
    """Live collection must use the same active ETF universe as production nightly scrape."""
    monkeypatch.setattr(
        live,
        "get_eligible_etf_codes",
        lambda requested_date: pytest.fail(
            "historical analysis universe must not drive live scraper collection"
        ),
    )
    monkeypatch.setattr(
        live,
        "get_active_etfs",
        lambda as_of_date: [{"code": "0001A"}, {"code": "0002A"}],
        raising=False,
    )

    config = live._OfflineConfig(live_date="2026-08-12")
    metafunc = live._OfflineMetafunc(config)

    live.pytest_generate_tests(metafunc)

    assert metafunc.generated is not None
    _, cases = metafunc.generated
    assert [case.values for case in cases] == [
        ("0001A", "moneydj"),
        ("0001A", "official"),
        ("0002A", "moneydj"),
        ("0002A", "official"),
    ]


def test_db_unchanged_hash_covers_non_snapshot_tables(monkeypatch):
    """Changing ETF configuration must be visible to the DB unchanged guard."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE etf_daily_holdings (id INTEGER PRIMARY KEY, value TEXT)")
    conn.execute(
        "CREATE TABLE etf_daily_non_stock_assets (id INTEGER PRIMARY KEY, value TEXT)"
    )
    conn.execute("CREATE TABLE etf_universe (code TEXT PRIMARY KEY, issuer TEXT)")
    conn.execute("INSERT INTO etf_daily_holdings VALUES (1, 'holding')")
    conn.execute("INSERT INTO etf_daily_non_stock_assets VALUES (1, 'cash')")
    conn.execute("INSERT INTO etf_universe VALUES ('0001A', 'Issuer A')")
    conn.commit()

    monkeypatch.setattr(live.db, "_connect", lambda: conn)
    hashes = getattr(live, "_operational_db_table_hashes", live._snapshot_table_hashes)

    before = hashes()
    conn.execute("UPDATE etf_universe SET issuer = 'Issuer B' WHERE code = '0001A'")
    conn.commit()
    after = hashes()

    assert before != after
