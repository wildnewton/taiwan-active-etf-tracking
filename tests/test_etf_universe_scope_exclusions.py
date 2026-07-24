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
        "official_url": "https://www.fhtrust.com.tw/ETF/trade_list",
        "official_method": "playwright",
        "official_logic": official_logic,
    })


def _discovered(code="00998A", name="主動復華金融股息", market="TPEx"):
    return [{"code": code, "name": name, "market": market, "isin": None}]


def test_upsert_preserves_manual_status_when_metadata_update_omits_it():
    db.init_db(":memory:")
    from etf_universe import get_etf_config, upsert_etf

    upsert_etf({
        "code": "00998A",
        "name": "主動復華金融股息",
        "retired": 1,
        "first_seen_date": "2026-07-01",
        "official_logic": "excluded_from_taiwan_stock_universe",
    })
    upsert_etf({"code": "00998A", "name": "手動更新名稱"})

    config = get_etf_config("00998A")

    assert config["name"] == "手動更新名稱"
    assert config["retired"] == 1
    assert config["official_logic"] == "excluded_from_taiwan_stock_universe"


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
    assert "00998A" not in active_codes


def test_reconcile_also_preserves_normal_manual_retirement():
    db.init_db(":memory:")
    from etf_universe import get_active_etfs, get_etf_config, reconcile_discovered_universe, retire_etf, upsert_etf

    upsert_etf({"code": "00980A", "name": "主動野村臺灣優選", "market": "TWSE"})
    retire_etf("00980A", reason="not listed")

    summary = reconcile_discovered_universe(
        _discovered("00980A", "主動野村臺灣優選", "TWSE"),
        seen_date="2026-07-02",
    )
    config = get_etf_config("00980A")
    active_codes = {row["code"] for row in get_active_etfs()}

    assert summary["reactivated"] == []
    assert config["retired"] == 1
    assert "00980A" not in active_codes
