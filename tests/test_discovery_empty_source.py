from discover_active_etfs import ListedSecurity, discover_active_etfs_with_status


TWSE_SOURCE = {"market": "TWSE", "url": "https://twse.test"}
TPEX_SOURCE = {"market": "TPEx", "url": "https://tpex.test"}


def test_empty_source_rows_mark_discovery_incomplete(monkeypatch):
    def fake_fetch(source):
        if source["market"] == "TWSE":
            return [ListedSecurity(market="TWSE", code="00980A", name="主動測試ETF", isin=None)]
        return []

    monkeypatch.setattr("discover_active_etfs.fetch_security_master", fake_fetch)

    result = discover_active_etfs_with_status(sources=[TWSE_SOURCE, TPEX_SOURCE])

    assert result.discovery_complete is False
    assert result.completed_markets == ["TWSE"]
    assert result.failed_markets == [{"market": "TPEx", "reason": "empty source result"}]
    assert [row["code"] for row in result.discovered] == ["00980A"]
