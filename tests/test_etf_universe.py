import importlib

import pytest

import db


EXPECTED_CODES = {
    "00400A",
    "00401A",
    "00403A",
    "00404A",
    "00405A",
    "00406A",
    "00980A",
    "00981A",
    "00982A",
    "00984A",
    "00985A",
    "00987A",
    "00991A",
    "00992A",
    "00993A",
    "00994A",
    "00995A",
    "00996A",
    "00999A",
}


def test_config_no_longer_exports_tracked_etfs():
    config = importlib.import_module("config")

    assert not hasattr(config, "TRACKED_ETFS")


def test_init_db_creates_etf_universe_table():
    db.init_db(":memory:")

    with db._connect() as conn:
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(etf_universe)").fetchall()
        }

    assert columns == {
        "code",
        "name",
        "issuer",
        "market",
        "isin",
        "retired",
        "first_seen_date",
        "last_seen_date",
        "retired_since",
        "official_url",
        "official_method",
        "official_logic",
        "created_at",
        "updated_at",
    }


def test_seed_etf_universe_from_seed_file_populates_known_etfs():
    db.init_db(":memory:")
    from etf_universe import get_active_etfs, seed_etf_universe_from_file

    inserted = seed_etf_universe_from_file()
    rows = get_active_etfs()

    assert inserted == 19
    assert len(rows) == 19
    assert {row["code"] for row in rows} == EXPECTED_CODES
    assert next(row for row in rows if row["code"] == "00980A")["issuer"] == "Nomura"


def test_seed_is_idempotent_and_preserves_existing_db_metadata():
    db.init_db(":memory:")
    from etf_universe import get_etf_config, seed_etf_universe_from_file, upsert_etf

    seed_etf_universe_from_file()
    upsert_etf({"code": "00980A", "name": "手動名稱", "issuer": "ManualIssuer"})
    inserted_again = seed_etf_universe_from_file()
    config = get_etf_config("00980A")

    assert inserted_again == 0
    assert config["name"] == "手動名稱"
    assert config["issuer"] == "ManualIssuer"


def test_get_active_etfs_excludes_retired_rows():
    db.init_db(":memory:")
    from etf_universe import get_active_etfs, retire_etf, seed_etf_universe_from_file

    seed_etf_universe_from_file()
    retire_etf("00980A", retired_since="2026-07-01", reason="not listed")

    active_codes = {row["code"] for row in get_active_etfs()}

    assert "00980A" not in active_codes
    assert len(active_codes) == 18


def test_get_etf_config_can_return_retired_for_historical_lookup():
    db.init_db(":memory:")
    from etf_universe import get_etf_config, retire_etf, seed_etf_universe_from_file

    seed_etf_universe_from_file()
    retire_etf("00980A", retired_since="2026-07-01", reason="not listed")

    assert get_etf_config("00980A")["code"] == "00980A"
    with pytest.raises(KeyError):
        get_etf_config("NOPE")


def test_reconcile_discovery_inserts_new_and_retires_missing():
    db.init_db(":memory:")
    from etf_universe import get_active_etfs, get_etf_config, reconcile_discovered_universe, seed_etf_universe_from_file

    seed_etf_universe_from_file()
    discovered = [
        {"code": code, "name": f"ETF {code}", "market": "TWSE", "isin": f"ISIN{code}"}
        for code in sorted(EXPECTED_CODES - {"00980A"})
    ]
    discovered.append({"code": "01000A", "name": "主動測試新ETF", "market": "TWSE", "isin": "TW00001000A"})

    summary = reconcile_discovered_universe(discovered, seen_date="2026-07-01")
    active_codes = {row["code"] for row in get_active_etfs()}
    new_config = get_etf_config("01000A")
    retired_config = get_etf_config("00980A")

    assert summary["inserted"] == ["01000A"]
    assert summary["retired"] == ["00980A"]
    assert "01000A" in active_codes
    assert "00980A" not in active_codes
    assert new_config["official_method"] is None
    assert retired_config["retired"] == 1
    assert retired_config["retired_since"] == "2026-07-01"


def test_reconcile_discovery_reactivates_retired_etf():
    db.init_db(":memory:")
    from etf_universe import get_active_etfs, reconcile_discovered_universe, retire_etf, seed_etf_universe_from_file

    seed_etf_universe_from_file()
    retire_etf("00980A", retired_since="2026-07-01", reason="not listed")
    discovered = [
        {"code": code, "name": f"ETF {code}", "market": "TWSE", "isin": f"ISIN{code}"}
        for code in sorted(EXPECTED_CODES)
    ]

    summary = reconcile_discovered_universe(discovered, seen_date="2026-07-02")
    active_codes = {row["code"] for row in get_active_etfs()}

    assert summary["reactivated"] == ["00980A"]
    assert "00980A" in active_codes


def test_pipeline_fetches_only_not_retired_etfs_from_db():
    db.init_db(":memory:")
    from etf_universe import retire_etf, seed_etf_universe_from_file
    from pipeline import run_daily_scrape

    seed_etf_universe_from_file()
    retire_etf("00980A", retired_since="2026-07-01", reason="not listed")
    seen_codes = []

    def fake_scrape(code):
        seen_codes.append(code)
        return {
            "ok": False,
            "reason": "test",
            "all_rows": [],
            "stock_rows": [],
            "non_stock_rows": [],
            "source_url": "",
            "source_type": "",
            "total_weight_all_rows": 0.0,
            "total_weight_stock_rows": 0.0,
        }

    import pipeline

    pipeline.scrape_holdings = fake_scrape
    summary = run_daily_scrape(":memory:")

    assert "00980A" not in seen_codes
    assert len(seen_codes) == 18
    assert summary["total_etfs"] == 18
