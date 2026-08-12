from datetime import date

import pytest

import etf_universe
from scrapers.official import scrape_allianz_api
from snapshot_validation import validate_snapshot_rows


@pytest.mark.live
def test_allianz_live_scraper_returns_valid_snapshots(allianz_live_date: date):
    allianz_etfs = [
        etf
        for etf in etf_universe.get_active_etfs(allianz_live_date)
        if etf.get("issuer") == "Allianz"
        and etf.get("official_method") in {"api", "playwright", "browser", "stealth_api"}
    ]

    assert allianz_etfs, "no active Allianz ETFs found in operational configuration"

    for etf in allianz_etfs:
        etf_code = etf["code"]
        result = scrape_allianz_api(etf_code, allianz_live_date)

        assert result["ok"] is True, f"{etf_code}: {result['reason']}"
        assert result["stock_rows"], f"{etf_code}: no stock rows"
        assert result["source_type"] == "official_fallback"
        assert all(row["etf_code"] == etf_code for row in result["stock_rows"])
        assert all(
            row["date"] == allianz_live_date.strftime("%Y/%m/%d")
            for row in result["stock_rows"]
        )

        valid, reason = validate_snapshot_rows(result["all_rows"])
        assert valid, f"{etf_code}: snapshot validation failed: {reason}"
