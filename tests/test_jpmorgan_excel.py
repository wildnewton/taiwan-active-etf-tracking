import asyncio
from datetime import date
from io import BytesIO
from unittest.mock import AsyncMock, Mock

import pytest
from openpyxl import Workbook

import scraper
from scrapers import official


TARGET_DATE = date(2026, 7, 22)
JPMORGAN_URL = "https://am.jpmorgan.com/FundsMarketingHandler/excel"
_CONFIG = {
    "url": JPMORGAN_URL,
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


def _xlsx(data_date="2026-07-22"):
    tables = {
        "基金資產 - 股票": [
            ("股票代碼", "股票名稱", "股數", "金額", "權重 (%)"),
            ("2330", "台灣積體電路製造", "110,000", "264,000,000", "8.66%"),
            ("2454", "聯發科技", "57,600", "221,760,000", "7.27%"),
            ("2308", "台達電子工業", "68,000", "127,840,000", "4.19%"),
            ("2345", "智邦科技", "36,053", "82,381,105", "2.70%"),
            ("2382", "廣達電腦", "193,000", "63,497,000", "2.08%"),
        ],
        "基金資產 - 期貨": [
            ("商品代碼", "商品名稱", "商品數量 (口數)", "權重 (%)"),
            ("FTQ6", "臺股期貨08/26", "24", "-0.18%"),
        ],
        "基金資產 - 選擇權": [
            ("商品代碼", "商品名稱", "商品數量 (口數)", "權重 (%)"),
            ("TWSE 08/19/26 C45600", "臺指選擇權08/26 45600 買權", "-310", "-0.61%"),
        ],
        "現金與約當現金": [
            ("名稱", "金額 (TWD)", "權重 (%)"),
            ("NEW TAIWAN DOLLAR", "283,733,974", "9.30%"),
        ],
    }
    workbook = Workbook()
    workbook.remove(workbook.active)
    for sheet_name, rows in tables.items():
        sheet = workbook.create_sheet(sheet_name)
        sheet.append([f"{sheet_name} ({data_date})"])
        for row in rows:
            sheet.append(row)
    content = BytesIO()
    workbook.save(content)
    workbook.close()
    return content.getvalue()


class _Response:
    def __init__(self):
        self.content = _xlsx()
        self.url = (
            f"{JPMORGAN_URL}?type=holding_pcf&cusip=TW00000401A1&"
            "country=tw&role=twetf&locale=zh-TW&date=2026-07-22"
        )

    def raise_for_status(self):
        return None


def test_parse_jpmorgan_excel_reads_all_asset_sheets():
    rows = official.parse_jpmorgan_excel(
        _xlsx(),
        "00401A",
        "https://example.test/holding.xlsx",
        TARGET_DATE,
    )

    assert [row["stock_code"] for row in rows[:5]] == [
        "2330", "2454", "2308", "2345", "2382"
    ]
    assert [row["asset_type"] for row in rows[5:]] == ["futures", "options", "cash"]
    assert rows[0]["shares"] == 110000
    assert rows[0]["market_value"] == 264000000
    assert rows[-1]["market_value"] == 283733974
    assert {row["date"] for row in rows} == {"2026/07/22"}


def test_parse_jpmorgan_excel_rejects_date_mismatch():
    with pytest.raises(ValueError, match="date mismatch"):
        official.parse_jpmorgan_excel(
            _xlsx("2026-07-21"),
            "00401A",
            "https://example.test/holding.xlsx",
            TARGET_DATE,
        )


def test_scrape_jpmorgan_excel_downloads_requested_date(monkeypatch):
    calls = {}

    def fake_get(url, *, params, headers, timeout):
        calls.update(url=url, params=params, headers=headers, timeout=timeout)
        return _Response()

    monkeypatch.setattr(official, "get_official_config", lambda code: _CONFIG)
    monkeypatch.setattr(official.requests, "get", fake_get)

    result = official.scrape_jpmorgan_excel("00401A", TARGET_DATE)

    assert result["ok"] is True
    assert (len(result["stock_rows"]), len(result["non_stock_rows"])) == (5, 3)
    assert calls["url"] == JPMORGAN_URL
    assert calls["params"]["date"] == "2026-07-22"
    assert calls["params"]["cusip"] == "TW00000401A1"
    assert result["source_url"] == _Response().url


def test_scrape_jpmorgan_excel_fails_closed_on_download_error(monkeypatch):
    monkeypatch.setattr(official, "get_official_config", lambda code: _CONFIG)
    monkeypatch.setattr(
        official.requests,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("network down")),
    )

    result = official.scrape_jpmorgan_excel("00401A", TARGET_DATE)

    assert result["ok"] is False
    assert "network down" in result["reason"]
    assert result["all_rows"] == []


def test_jpmorgan_dispatcher_uses_excel_without_page(monkeypatch):
    handler = Mock(return_value={"ok": True})
    monkeypatch.setattr(official, "get_official_config", lambda code: _CONFIG)
    monkeypatch.setattr(official, "scrape_jpmorgan_excel", handler)

    result = asyncio.run(
        official.scrape_official_with_browser(
            "00401a",
            object(),
            target_date=TARGET_DATE,
        )
    )

    assert result == {"ok": True}
    handler.assert_called_once_with("00401A", TARGET_DATE)


@pytest.mark.parametrize(
    ("etf_code", "issuer", "expected_kwargs"),
    [
        ("00401A", "JPMorgan", {"target_date": TARGET_DATE}),
        ("00980A", "Capital", {}),
    ],
)
def test_official_fallback_passes_date_only_to_jpmorgan(
    monkeypatch,
    etf_code,
    issuer,
    expected_kwargs,
):
    page = object()
    browser = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(
        scraper,
        "get_etf_config",
        lambda code: {"official_method": "api", "issuer": issuer},
    )
    monkeypatch.setattr(scraper, "scrape_official_with_browser", browser)
    monkeypatch.setattr(scraper, "_normalize_source_result", lambda result, source: result)

    result = asyncio.run(
        scraper._official_fallback_with_browser(
            etf_code,
            page,
            target_date=TARGET_DATE,
        )
    )

    assert result == {"ok": True}
    browser.assert_awaited_once_with(etf_code, page, **expected_kwargs)
