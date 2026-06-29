from discover_active_etfs import (
    ListedSecurity,
    discover_active_etfs_with_status,
    is_discoverable_active_etf,
    trades_offshore_instruments,
)


TWSE_SOURCE = {"market": "TWSE", "url": "https://twse.test"}


def security(name, code="00980A", market="TWSE", isin=None):
    return ListedSecurity(market=market, code=code, name=name, isin=isin)


def test_domestic_taiwan_active_etf_is_included():
    row = security("主動統一台灣高息動能")

    assert trades_offshore_instruments(row) is False
    assert is_discoverable_active_etf(row) is True


def test_explicit_offshore_active_etfs_are_excluded():
    names = [
        "主動全球創新ETF",
        "主動美國科技ETF",
        "主動日本半導體ETF",
        "主動中國成長ETF",
        "主動越南機會ETF",
        "主動印度市場ETF",
        "主動歐洲收益ETF",
        "主動亞洲機會ETF",
        "主動境外股票ETF",
        "主動海外資產ETF",
    ]

    for name in names:
        row = security(name)
        assert trades_offshore_instruments(row) is True, name
        assert is_discoverable_active_etf(row) is False, name


def test_taiwan_issuer_name_does_not_create_china_false_positive():
    row = security("中國信託主動優質成長ETF")

    assert trades_offshore_instruments(row) is False
    assert is_discoverable_active_etf(row) is True


def test_domestic_taiwan_keywords_override_broad_terms():
    names = [
        "主動台灣全球供應鏈ETF",
        "主動臺灣亞洲科技ETF",
        "主動台股創新ETF",
        "主動臺股高息ETF",
    ]

    for name in names:
        row = security(name)
        assert trades_offshore_instruments(row) is False, name
        assert is_discoverable_active_etf(row) is True, name


def test_ambiguous_active_etf_without_offshore_keywords_remains_included():
    row = security("主動優質成長ETF")

    assert trades_offshore_instruments(row) is False
    assert is_discoverable_active_etf(row) is True


def test_discovery_status_uses_offshore_filter(monkeypatch):
    rows = [
        security("主動台灣成長ETF", code="00980A"),
        security("主動全球科技ETF", code="00981A"),
        security("元大台灣50", code="0050"),
    ]

    monkeypatch.setattr("discover_active_etfs.fetch_security_master", lambda source: rows)

    result = discover_active_etfs_with_status(sources=[TWSE_SOURCE])

    assert result.discovery_complete is True
    assert [row["code"] for row in result.discovered] == ["00980A"]
