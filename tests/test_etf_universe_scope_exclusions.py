import db


def _upsert_scope_excluded_etf(code="00998A", official_logic="excluded_from_taiwan_stock_universe"):
    from etf_universe import upsert_etf

    upsert_etf({
        "code": code,
        "name": "主動復華金融股息",
        "issuer": "FuhHwa",
        "market": "TPEx",
        "isin": None,
        "retired": 1,
        "first_seen_date": "2026-07-01",
        "last_active_date": "2026-07-01",
        "pending_retirement_since": None,
        "official_url": "https://www.fhtrust.com.tw/ETF/trade_list",
        "official_method": "playwright",
        "official_logic": official_logic,
    })


def _discovered(code="00998A", name="主動復華金融股息", market="TPEx"):
    return [{"code": code, "name": name, "market": market, "isin": None}]


def test_reconcile_does_not_reactivate_taiwan_scope_excluded_retired_etf():
    db.init_db(":memory:")
    from etf_universe import get_active_etfs, get_etf_config, reconcile_discovered_universe

    _upsert_scope_excluded_etf(
        official_logic="excluded_from_taiwan_stock_universe;prospectus=global_financial_active_etf"
    )

    summary = reconcile_discovered_universe(_discovered(), seen_date="2026-07-02")
    config = get_etf_config("00998A")
    active_codes = {row["code"] for row in get_active_etfs()}

    assert "00998A" not in summary["reactivated"]
    assert config["retired"] == 1
    assert config["last_active_date"] == "2026-07-01"
    assert "00998A" not in active_codes


def test_reconcile_does_not_reactivate_retired_etf_marked_as_offshore_instruments():
    db.init_db(":memory:")
    from etf_universe import get_active_etfs, get_etf_config, reconcile_discovered_universe

    _upsert_scope_excluded_etf(
        official_logic="trades_offshore_instruments=true;prospectus=global_financial_active_etf"
    )

    summary = reconcile_discovered_universe(_discovered(), seen_date="2026-07-02")
    config = get_etf_config("00998A")
    active_codes = {row["code"] for row in get_active_etfs()}

    assert "00998A" not in summary["reactivated"]
    assert config["retired"] == 1
    assert config["last_active_date"] == "2026-07-01"
    assert "00998A" not in active_codes


def test_reconcile_still_reactivates_normal_retired_etf_when_rediscovered():
    db.init_db(":memory:")
    from etf_universe import get_active_etfs, get_etf_config, reconcile_discovered_universe, retire_etf, seed_etf_universe_from_file

    seed_etf_universe_from_file()
    retire_etf("00980A", last_active_date="2026-07-01", reason="not listed")

    summary = reconcile_discovered_universe(
        _discovered("00980A", "主動野村臺灣優選", "TWSE"),
        seen_date="2026-07-02",
    )
    config = get_etf_config("00980A")
    active_codes = {row["code"] for row in get_active_etfs()}

    assert summary["reactivated"] == ["00980A"]
    assert config["retired"] == 0
    assert config["last_active_date"] == "2026-07-02"
    assert "00980A" in active_codes
