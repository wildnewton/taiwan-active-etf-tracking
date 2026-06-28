import pytest

from discover_active_etfs import (
    ListedSecurity,
    discover_active_etfs_with_status,
    discover_and_reconcile,
)


TWSE_SOURCE = {"market": "TWSE", "url": "https://twse.test"}
TPEX_SOURCE = {"market": "TPEx", "url": "https://tpex.test"}


def _security(market, code, name="主動測試ETF", isin=None):
    return ListedSecurity(market=market, code=code, name=name, isin=isin)


def test_per_source_discovery_reports_complete_metadata(monkeypatch):
    def fake_fetch(source):
        if source["market"] == "TWSE":
            return [_security("TWSE", "00980A"), _security("TWSE", "0050", "元大台灣50")]
        return [_security("TPEx", "00981A")]

    monkeypatch.setattr("discover_active_etfs.fetch_security_master", fake_fetch)

    result = discover_active_etfs_with_status(sources=[TWSE_SOURCE, TPEX_SOURCE])

    assert result.discovery_complete is True
    assert result.completed_markets == ["TPEx", "TWSE"]
    assert result.failed_markets == []
    assert [row["code"] for row in result.discovered] == ["00980A", "00981A"]


def test_one_source_failure_returns_partial_discovery_not_exception(monkeypatch):
    def fake_fetch(source):
        if source["market"] == "TWSE":
            return [_security("TWSE", "00980A")]
        raise RuntimeError("TPEx timeout")

    monkeypatch.setattr("discover_active_etfs.fetch_security_master", fake_fetch)

    result = discover_active_etfs_with_status(sources=[TWSE_SOURCE, TPEX_SOURCE])

    assert result.discovery_complete is False
    assert result.completed_markets == ["TWSE"]
    assert result.failed_markets == [{"market": "TPEx", "reason": "TPEx timeout"}]
    assert [row["code"] for row in result.discovered] == ["00980A"]


def test_discover_and_reconcile_passes_partial_completeness_to_reconciliation(monkeypatch, tmp_path):
    captured = {}

    def fake_discover(sources=None):
        from discover_active_etfs import DiscoveryResult
        return DiscoveryResult(
            discovered=[{"code": "00980A", "name": "主動測試ETF", "market": "TWSE", "isin": None}],
            completed_markets=["TWSE"],
            failed_markets=[{"market": "TPEx", "reason": "timeout"}],
            expected_markets=["TWSE", "TPEx"],
        )

    def fake_reconcile(discovered, seen_date=None, discovery_complete=True):
        captured["discovered"] = discovered
        captured["seen_date"] = seen_date
        captured["discovery_complete"] = discovery_complete
        return {"inserted": [], "retired": [], "active_total": 19}

    monkeypatch.setattr("discover_active_etfs.discover_active_etfs_with_status", fake_discover)
    monkeypatch.setattr("discover_active_etfs.reconcile_discovered_universe", fake_reconcile)

    summary = discover_and_reconcile(tmp_path / "universe.sqlite", seen_date="2026-07-03")

    assert captured["discovery_complete"] is False
    assert captured["seen_date"] == "2026-07-03"
    assert captured["discovered"] == [{"code": "00980A", "name": "主動測試ETF", "market": "TWSE", "isin": None}]
    assert summary["discovery_complete"] is False
    assert summary["failed_markets"] == [{"market": "TPEx", "reason": "timeout"}]
    assert summary["completed_markets"] == ["TWSE"]
