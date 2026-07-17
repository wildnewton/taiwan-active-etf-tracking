import importlib
import sqlite3
from unittest.mock import patch

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


def _discovered_without(*missing_codes):
    missing = set(missing_codes)
    return [
        {"code": code, "name": f"ETF {code}", "market": "TWSE", "isin": f"ISIN{code}"}
        for code in sorted(EXPECTED_CODES - missing)
    ]


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
        "listing_date",
        "retired",
        "first_seen_date",
        "last_active_date",
        "pending_retirement_since",
        "official_url",
        "official_method",
        "official_logic",
        "created_at",
        "updated_at",
    }
    assert "last_seen_date" not in columns
    assert "retired_since" not in columns


def test_init_db_migrates_legacy_seen_and_retired_dates_to_last_active_date(tmp_path):
    db_path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE etf_universe (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                issuer TEXT,
                market TEXT,
                isin TEXT,
                retired INTEGER NOT NULL DEFAULT 0,
                first_seen_date TEXT,
                last_seen_date TEXT,
                retired_since TEXT,
                pending_retirement_since TEXT,
                official_url TEXT,
                official_method TEXT,
                official_logic TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO etf_universe (
                code, name, retired, first_seen_date, last_seen_date, retired_since,
                created_at, updated_at
            ) VALUES
                ('ACTIVE', 'Active ETF', 0, '2026-07-01', '2026-07-03', NULL, 'c', 'u'),
                ('RETIRED', 'Retired ETF', 1, '2026-07-01', '2026-07-03', '2026-07-04', 'c', 'u')
            """
        )

    db.init_db(str(db_path))

    with db._connect() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(etf_universe)").fetchall()}
        rows = {
            row[0]: row[1]
            for row in conn.execute("SELECT code, last_active_date FROM etf_universe")
        }

    assert "last_active_date" in columns
    assert "last_seen_date" not in columns
    assert "retired_since" not in columns
    assert rows == {"ACTIVE": "2026-07-03", "RETIRED": "2026-07-03"}


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

    seed_etf_universe_from_file(seen_date="2026-06-30")
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
    retire_etf("00980A", last_active_date="2026-07-01", reason="not listed")

    active_codes = {row["code"] for row in get_active_etfs()}

    assert "00980A" not in active_codes
    assert len(active_codes) == 18


def test_get_etf_config_can_return_retired_for_historical_lookup():
    db.init_db(":memory:")
    from etf_universe import get_etf_config, retire_etf, seed_etf_universe_from_file

    seed_etf_universe_from_file()
    retire_etf("00980A", last_active_date="2026-07-01", reason="not listed")

    assert get_etf_config("00980A")["code"] == "00980A"
    with pytest.raises(KeyError):
        get_etf_config("NOPE")


def test_reconcile_discovery_inserts_new_and_marks_missing_pending_first():
    db.init_db(":memory:")
    from etf_universe import get_active_etfs, get_etf_config, reconcile_discovered_universe, seed_etf_universe_from_file

    seed_etf_universe_from_file(seen_date="2026-06-30")
    discovered = _discovered_without("00980A")
    discovered.append({"code": "01000A", "name": "主動測試新ETF", "market": "TWSE", "isin": "TW00001000A"})

    summary = reconcile_discovered_universe(discovered, seen_date="2026-07-01")
    active_codes = {row["code"] for row in get_active_etfs()}
    new_config = get_etf_config("01000A")
    pending_config = get_etf_config("00980A")

    assert summary["inserted"] == ["01000A"]
    assert summary["retired"] == []
    assert summary["pending_retirement"] == ["00980A"]
    assert "01000A" in active_codes
    assert "00980A" in active_codes
    assert new_config["official_method"] is None
    assert pending_config["retired"] == 0
    assert pending_config["pending_retirement_since"] == "2026-07-01"


def test_complete_second_consecutive_absence_retires_pending_etf():
    db.init_db(":memory:")
    from etf_universe import get_active_etfs, get_etf_config, reconcile_discovered_universe, seed_etf_universe_from_file

    seed_etf_universe_from_file(seen_date="2026-06-30")
    reconcile_discovered_universe(_discovered_without("00980A"), seen_date="2026-07-01")
    summary = reconcile_discovered_universe(_discovered_without("00980A"), seen_date="2026-07-02")

    active_codes = {row["code"] for row in get_active_etfs()}
    retired_config = get_etf_config("00980A")

    assert summary["retired"] == ["00980A"]
    assert "00980A" not in active_codes
    assert retired_config["retired"] == 1
    assert retired_config["last_active_date"] == "2026-06-30"
    assert retired_config["pending_retirement_since"] is None


def test_incomplete_discovery_does_not_mark_or_retire_missing_etfs():
    db.init_db(":memory:")
    from etf_universe import get_active_etfs, get_etf_config, reconcile_discovered_universe, seed_etf_universe_from_file

    seed_etf_universe_from_file(seen_date="2026-06-30")
    summary = reconcile_discovered_universe(
        _discovered_without("00980A"),
        seen_date="2026-07-01",
        discovery_complete=False,
    )

    active_codes = {row["code"] for row in get_active_etfs()}
    config = get_etf_config("00980A")

    assert summary["retired"] == []
    assert summary["pending_retirement"] == []
    assert summary["retirement_skipped"] == ["00980A"]
    assert "00980A" in active_codes
    assert config["pending_retirement_since"] is None
    assert config["retired"] == 0


def test_reappearance_clears_pending_retirement():
    db.init_db(":memory:")
    from etf_universe import get_etf_config, reconcile_discovered_universe, seed_etf_universe_from_file

    seed_etf_universe_from_file(seen_date="2026-06-30")
    reconcile_discovered_universe(_discovered_without("00980A"), seen_date="2026-07-01")
    summary = reconcile_discovered_universe(_discovered_without(), seen_date="2026-07-02")

    config = get_etf_config("00980A")

    assert summary["updated"] == sorted(EXPECTED_CODES)
    assert config["retired"] == 0
    assert config["pending_retirement_since"] is None


def test_reconcile_discovery_reactivates_retired_etf_and_clears_pending_state():
    db.init_db(":memory:")
    from etf_universe import get_active_etfs, get_etf_config, reconcile_discovered_universe, retire_etf, seed_etf_universe_from_file

    seed_etf_universe_from_file()
    retire_etf("00980A", last_active_date="2026-07-01", reason="not listed")
    discovered = _discovered_without()

    summary = reconcile_discovered_universe(discovered, seen_date="2026-07-02")
    active_codes = {row["code"] for row in get_active_etfs()}
    config = get_etf_config("00980A")

    assert summary["reactivated"] == ["00980A"]
    assert "00980A" in active_codes
    assert config["last_active_date"] == "2026-07-02"
    assert config["pending_retirement_since"] is None


def test_pipeline_fetches_only_not_retired_etfs_from_db(tmp_path):
    db_path = str(tmp_path / "universe.sqlite")
    db.init_db(db_path)
    from etf_universe import retire_etf, seed_etf_universe_from_file
    from pipeline import run_daily_scrape

    seed_etf_universe_from_file()
    retire_etf("00980A", last_active_date="2026-07-01", reason="not listed")
    seen_codes = []

    def fake_scrape(code, target_date=None):
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

    with patch("pipeline.scrape_holdings", side_effect=fake_scrape), \
        patch("pipeline.latest_tw_trading_day_on_or_before", side_effect=lambda run_date: run_date):
        summary = run_daily_scrape(db_path)

    assert "00980A" not in seen_codes
    assert len(seen_codes) == 18
    assert summary["total_etfs"] == 18
