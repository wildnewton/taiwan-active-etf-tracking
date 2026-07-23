import asyncio
from datetime import date
from unittest.mock import AsyncMock, Mock

import pytest

import scraper
from scrapers import official


_CONFIG = {
    "url": "https://am.jpmorgan.com/FundsMarketingHandler/excel",
    "method": "api",
    "issuer": "JPMorgan",
    "internal_ids": {
        "type": "holding_pcf",
        "cusip": "TW00000401A1",
        "country": "tw",
        "role": "twetf",
        "locale": "zh-TW",
    },
}


class _Sheet:
    def __init__(self, rows):
        self._rows = rows

    def iter_rows(self, values_only=False):
        assert values_only is True
        return iter(self._rows)


class _Workbook:
    def __init__(self, sheet_rows):
        self._sheets = {
            name: _Sheet(rows)
            for name, rows in sheet_rows.items()
        }
        self.sheetnames = list(self._sheets)

    def __getitem__(self, name):
        return self._sheets[name]


class _Response:
    content = b"xlsx"
    url = (
        "https://am.jpmorgan.com/FundsMarketingHandler/excel?"
        "type=holding_pcf&cusip=TW00000401A1&country=tw&role=twetf&"
        "locale=zh-TW&date=2026-07-22"
    )

    def raise_for_status(self):
        return None


def _workbook(data_date="2026-07-22"):
    return _Workbook(
        {
            "基金資產 - 股票": [
                (f"基金資產 - 股票 ({data_date})", None, None, None, None),
                ("股票代碼", "股票名稱", "股數", "金額", "權重 (%)"),
                ("2330", "台灣積體電路製造", "110,000", "264,000,000", "8.66%"),
                ("2454", "聯發科技", "57,600", "221,760,000", "7.27%"),
                ("2308", "台達電子工業", "68,000", "127,840,000", "4.19%"),
                ("2345", "智邦科技", "36,053", "82,381,105", "2.70%"),
                ("2382", "廣達電腦", "193,000", "63,497,000", "2.08%"),
            ],
            "基金資產 - 期貨": [
                (f"基金資產 - 期貨 ({data_date})", None, None, None),
                ("商品代碼", "商品名稱", "商品數量 (口數)", "權重 (%)"),
                ("FTQ6", "臺股期貨08/26", "24", "-0.18%"),
            ],
            "基金資產 - 選擇權": [
                (f"基金資產 - 選擇權 ({data_date})", None, None, None),
                ("商品代碼", "商品名稱", "商品數量 (口數)", "權重 (%)"),
                ("TWSE 08/19/26 C45600", "臺指選擇權08/26 45600 買權", "-310", "-0.61%"),
            ],
            "現金與約當現金": [
                (f"現金與約當現金 ({data_date})", None, None),
                ("名稱", "金額 (TWD)", "權重 (%)"),
                ("NEW TAIWAN DOLLAR", "283,733,974", "9.30%"),
            ],
        }
    )


def _patch_workbook(monkeypatch, data_date="2026-07-22"):
    monkeypatch.setattr(
        official,
        "load_workbook",
        lambda *args, **kwargs: _workbook(data_date),
        raising=False,
    )


def test_parse_jpmorgan_excel_reads_all_asset_sheets(monkeypatch):
    _patch_workbook(monkeypatch)

    rows = official.parse_jpmorgan_excel(
        b"xlsx",
        "00401A",
        "https://example.test/holding.xlsx",
        date(2026, 7, 22),
    )

    assert [row["stock_code"] for row in rows[:5]] == [
        "2330",
        "2454",
        "2308",
        "2345",
        "2382",
    ]
    assert [row["asset_type"] for row in rows[5:]] == ["futures", "options", "cash"]
    assert rows[0]["shares"] == 110000
    assert rows[0]["market_value"] == 264000000
    assert rows[-1]["market_value"] == 283733974
    assert {row["date"] for row in rows} == {"2026/07/22"}


def test_parse_jpmorgan_excel_rejects_date_mismatch(monkeypatch):
    _patch_workbook(monkeypatch, "2026-07-21")

    with pytest.raises(ValueError, match="date mismatch"):
        official.parse_jpmorgan_excel(
            b"xlsx",
            "00401A",
            "https://example.test/holding.xlsx",
            date(2026, 7, 22),
        )


def test_scrape_jpmorgan_excel_downloads_requested_date(monkeypatch):
    _patch_workbook(monkeypatch)
    calls = {}

    def fake_get(url, *, params, headers, timeout):
        calls.update(url=url, params=params, headers=headers, timeout=timeout)
        return _Response()

    monkeypatch.setattr(official, "get_official_config", lambda code: _CONFIG)
    monkeypatch.setattr(official.requests, "get", fake_get)

    result = official.scrape_jpmorgan_excel("00401A", date(2026, 7, 22))

    assert result["ok"] is True
    assert len(result["stock_rows"]) == 5
    assert len(result["non_stock_rows"]) == 3
    assert calls["url"] == _CONFIG["url"]
    assert calls["params"]["date"] == "2026-07-22"
    assert calls["params"]["cusip"] == "TW00000401A1"
    assert result["source_url"] == _Response.url


def test_scrape_jpmorgan_excel_fails_closed_on_download_error(monkeypatch):
    monkeypatch.setattr(official, "get_official_config", lambda code: _CONFIG)
    monkeypatch.setattr(
        official.requests,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("network down")),
    )

    result = official.scrape_jpmorgan_excel("00401A", date(2026, 7, 22))

    assert result["ok"] is False
    assert "network down" in result["reason"]
    assert result["all_rows"] == []


def test_jpmorgan_dispatcher_uses_excel_without_page(monkeypatch):
    expected = {"ok": True}
    handler = Mock(return_value=expected)
    monkeypatch.setattr(official, "get_official_config", lambda code: _CONFIG)
    monkeypatch.setattr(official, "scrape_jpmorgan_excel", handler, raising=False)
    target = date(2026, 7, 22)

    result = asyncio.run(
        official.scrape_official_with_browser(
            "00401a",
            object(),
            target_date=target,
        )
    )

    assert result == expected
    handler.assert_called_once_with("00401A", target)


def test_official_fallback_forwards_target_date(monkeypatch):
    target = date(2026, 7, 22)
    page = object()
    browser = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(
        scraper,
        "get_etf_config",
        lambda code: {"official_method": "api"},
    )
    monkeypatch.setattr(scraper, "scrape_official_with_browser", browser)
    monkeypatch.setattr(scraper, "_normalize_source_result", lambda result, source: result)

    result = asyncio.run(
        scraper._official_fallback_with_browser(
            "00401A",
            page,
            target_date=target,
        )
    )

    assert result == {"ok": True}
    browser.assert_awaited_once_with("00401A", page, target_date=target)
