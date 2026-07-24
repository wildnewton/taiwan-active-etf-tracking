from pathlib import Path
from unittest.mock import patch

import pytest

import db


ROOT = Path(__file__).resolve().parents[1]


def _failed_scrape_result():
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


def test_empty_db_reads_do_not_seed_or_insert_rows():
    db.init_db(":memory:")
    from etf_universe import get_active_etfs, get_eligible_etf_codes, get_etf_config

    assert get_active_etfs(as_of_date="2026-07-24") == []
    assert get_eligible_etf_codes("2026-07-24") == []
    with pytest.raises(KeyError):
        get_etf_config("NOPE")

    with db._connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM etf_universe").fetchone()[0]

    assert count == 0


def test_pipeline_uses_only_explicit_db_universe(tmp_path):
    db_path = str(tmp_path / "universe.sqlite")
    db.init_db(db_path)
    from etf_universe import upsert_etf
    from pipeline import run_daily_scrape

    upsert_etf(
        {
            "code": "01000A",
            "name": "主動測試ETF",
            "issuer": "TestIssuer",
            "market": "TWSE",
            "listing_date": "2026-07-01",
        }
    )
    seen_codes = []

    def fake_scrape(code, target_date=None):
        seen_codes.append(code)
        return _failed_scrape_result()

    with patch("pipeline.scrape_holdings", side_effect=fake_scrape), patch(
        "pipeline.latest_tw_trading_day_on_or_before",
        side_effect=lambda run_date: run_date,
    ):
        summary = run_daily_scrape(db_path)

    assert seen_codes == ["01000A"]
    assert summary["total_etfs"] == 1


def test_discovery_can_create_the_only_rows_in_an_empty_db():
    db.init_db(":memory:")
    from etf_universe import get_active_etfs, reconcile_discovered_universe

    summary = reconcile_discovered_universe(
        [
            {
                "code": "01000A",
                "name": "主動測試ETF",
                "issuer": "TestIssuer",
                "market": "TWSE",
                "isin": "TW00001000A",
                "listing_date": "2026-07-01",
            }
        ],
        seen_date="2026-07-24",
    )

    assert summary["inserted"] == ["01000A"]
    assert {row["code"] for row in get_active_etfs("2026-07-24")} == {"01000A"}


def test_official_config_reflects_db_values_immediately():
    db.init_db(":memory:")
    from etf_universe import upsert_etf
    from scrapers.official import get_official_config

    upsert_etf(
        {
            "code": "01000A",
            "name": "主動測試ETF",
            "issuer": "TestIssuer",
            "official_url": "https://example.test/holdings-v1",
            "official_method": "static",
            "official_logic": "version=v1",
        }
    )

    first = get_official_config("01000A")
    assert first["url"] == "https://example.test/holdings-v1"
    assert first["method"] == "static"
    assert first["official_logic"] == "version=v1"

    upsert_etf(
        {
            "code": "01000A",
            "official_url": "https://example.test/holdings-v2",
            "official_method": "browser",
            "official_logic": "version=v2",
        }
    )

    updated = get_official_config("01000A")
    assert updated["url"] == "https://example.test/holdings-v2"
    assert updated["method"] == "browser"
    assert updated["official_logic"] == "version=v2"


def test_repository_has_no_etf_universe_seed_file():
    assert not (ROOT / "data" / "etf_universe_seed.json").exists()
