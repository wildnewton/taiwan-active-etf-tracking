import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

import scraper
from scrapers.official import (
    _is_capital_buyback_response,
    scrape_capital_playwright,
    scrape_mega_playwright,
    scrape_nomura_stealth,
    scrape_official_static,
)


CAPITAL_PAGE_URL = "https://www.capitalfund.com.tw/etf/product/detail/399/buyback"
CAPITAL_API_URL = "https://www.capitalfund.com.tw/CFWeb/api/etf/buyback"
NOMURA_PAGE_URL = (
    "https://www.nomurafunds.com.tw/ETFWEB/product-description"
    "?fundNo=00980A&tab=Shareholding"
)
NOMURA_API_URL = "https://www.nomurafunds.com.tw/API/ETFAPI/api/Fund/GetFundAssets"
MEGA_PAGE_URL = "https://www.megafunds.com.tw/MEGA/etf/etf_product.aspx?id=23"
STATIC_PAGE_URL = "https://websys.fsit.com.tw/FubonETF/Fund/Assets.aspx?stkId=00405A"


_STOCKS = [
    ("2330", "台積電"),
    ("2308", "台達電"),
    ("2454", "聯發科"),
    ("2317", "鴻海"),
    ("2382", "廣達"),
]


def _capital_payload(*, include_date=True):
    pcf = {"date2": "2026-07-16"} if include_date else {}
    return json.dumps(
        {
            "data": {
                "pcf": pcf,
                "stocks": [
                    {
                        "stocNo": code,
                        "stocName": name,
                        "share": 100000,
                        "weightRound": 20.0,
                    }
                    for code, name in _STOCKS
                ],
            }
        }
    )


def _nomura_payload(*, include_date=True):
    fund_asset = {"NavDate": "2026-07-16"} if include_date else {}
    return json.dumps(
        {
            "Entries": {
                "Data": {
                    "FundAsset": fund_asset,
                    "Table": [
                        {
                            "TableTitle": "股票",
                            "Rows": [
                                [code, name, "100000", "20.0"]
                                for code, name in _STOCKS
                            ],
                        }
                    ],
                }
            }
        }
    )


def _mega_text(*, include_date=True):
    lines = ["持股比重"]
    if include_date:
        lines.append("資料來源：兆豐投信，2026/07/16")
    for code, name in _STOCKS:
        lines.extend([code, name, "100,000", "20.0"])
    return "\n".join(lines)


def _static_html(*, include_date=True):
    date_html = "<p>資料日期：2026/07/16</p>" if include_date else ""
    rows = "".join(
        f"<tr><td>{code}</td><td>{name}</td><td>100,000</td><td>20%</td></tr>"
        for code, name in _STOCKS
    )
    return f"""
    <html><body>
      {date_html}
      <table>
        <thead><tr><th>股票代號</th><th>股票名稱</th><th>股數</th><th>權重</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </body></html>
    """


class _Request:
    def __init__(self, method):
        self.method = method


class _Response:
    def __init__(self, url, body, *, method="GET"):
        self.url = url
        self.ok = True
        self.request = _Request(method)
        self._body = body

    async def text(self):
        return self._body


class _ResponseInfo:
    def __init__(self, page):
        self._page = page

    @property
    def value(self):
        async def resolve():
            return self._page.selected_response

        return resolve()


class _ExpectResponseContext:
    def __init__(self, page, predicate):
        self._page = page
        self._predicate = predicate

    async def __aenter__(self):
        self._page.response_predicate = self._predicate
        return _ResponseInfo(self._page)

    async def __aexit__(self, exc_type, exc, traceback):
        if exc_type is None and self._page.selected_response is None:
            raise PlaywrightTimeoutError("timed out")
        return False


class _Page:
    def __init__(self, responses):
        self.responses = responses
        self.response_predicate = None
        self.selected_response = None
        self.goto_calls = []

    def expect_response(self, predicate, timeout):
        assert timeout <= 10000
        return _ExpectResponseContext(self, predicate)

    async def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))
        for response in self.responses:
            if self.response_predicate(response):
                self.selected_response = response
                break


def _mega_page(body_text):
    page = AsyncMock()
    page.goto = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    locator = Mock()
    locator.inner_text = AsyncMock(return_value=body_text)
    page.locator = Mock(return_value=locator)
    return page


def _successful_result():
    rows = [
        {
            "date": "2026/07/16",
            "etf_code": "00982A",
            "asset_name": f"{name}({code}.TW)",
            "asset_type": "stock",
            "stock_code": code,
            "stock_name": name,
            "shares": 100000,
            "weight_pct": 20.0,
            "source_url": STATIC_PAGE_URL,
            "source_type": "official_fallback",
            "extraction_method": "requests_bs4",
        }
        for code, name in _STOCKS
    ]
    return {
        "ok": True,
        "reason": "ok",
        "all_rows": rows,
        "stock_rows": rows,
        "non_stock_rows": [],
        "source_url": STATIC_PAGE_URL,
        "source_type": "official_fallback",
        "total_weight_all_rows": 100.0,
        "total_weight_stock_rows": 100.0,
    }


def test_capital_response_predicate_is_endpoint_specific():
    assert _is_capital_buyback_response(
        _Response(CAPITAL_PAGE_URL, "<html></html>")
    ) is False
    assert _is_capital_buyback_response(
        _Response(CAPITAL_API_URL, "{}", method="POST")
    ) is True


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_capital_ignores_document_response_before_api(mock_config):
    mock_config.return_value = {
        "url": CAPITAL_PAGE_URL,
        "method": "api",
        "issuer": "Capital",
    }
    page = _Page(
        [
            _Response(CAPITAL_PAGE_URL, "<html>product page</html>"),
            _Response(CAPITAL_API_URL, _capital_payload(), method="POST"),
        ]
    )

    result = await scrape_capital_playwright("00982A", page)

    assert result["ok"] is True
    assert result["stock_rows"][0]["stock_code"] == "2330"
    assert page.selected_response.url == CAPITAL_API_URL


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_capital_rejects_structurally_valid_undated_rows(mock_config):
    mock_config.return_value = {
        "url": CAPITAL_PAGE_URL,
        "method": "api",
        "issuer": "Capital",
    }
    page = _Page([_Response(CAPITAL_API_URL, _capital_payload(include_date=False), method="POST")])

    result = await scrape_capital_playwright("00982A", page)

    assert result["ok"] is False
    assert "date" in result["reason"].lower()


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_nomura_rejects_structurally_valid_undated_rows(mock_config):
    mock_config.return_value = {
        "url": NOMURA_PAGE_URL,
        "method": "stealth_api",
        "issuer": "Nomura",
    }
    page = _Page([_Response(NOMURA_API_URL, _nomura_payload(include_date=False), method="POST")])

    result = await scrape_nomura_stealth("00980A", page)

    assert result["ok"] is False
    assert "date" in result["reason"].lower()


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_mega_rejects_structurally_valid_undated_rows(mock_config):
    mock_config.return_value = {
        "url": MEGA_PAGE_URL,
        "method": "playwright",
        "issuer": "Mega",
    }
    page = _mega_page(_mega_text(include_date=False))

    result = await scrape_mega_playwright("00996A", page)

    assert result["ok"] is False
    assert "date" in result["reason"].lower()


def test_static_official_rejects_structurally_valid_undated_rows():
    config = {
        "url": STATIC_PAGE_URL,
        "method": "static",
        "issuer": "Fubon",
    }
    with patch("scrapers.official.get_official_config", return_value=config), patch(
        "scrapers.official.fetch_static",
        return_value=_static_html(include_date=False),
    ):
        result = scrape_official_static("00405A")

    assert result["ok"] is False
    assert "date" in result["reason"].lower()


@pytest.mark.asyncio
async def test_undated_capital_result_continues_to_static_fallback():
    page = _Page([_Response(CAPITAL_API_URL, _capital_payload(include_date=False), method="POST")])
    static_result = _successful_result()
    config = {
        "url": CAPITAL_PAGE_URL,
        "method": "api",
        "issuer": "Capital",
        "official_method": "api",
    }

    with patch("scraper.get_etf_config", return_value=config), patch(
        "scrapers.official.get_official_config",
        return_value=config,
    ), patch(
        "scraper._official_fallback_static",
        return_value=static_result,
    ) as static_fallback:
        result = await scraper._official_fallback_with_browser("00982A", page)

    static_fallback.assert_called_once_with("00982A")
    assert result == static_result


def test_legacy_listener_mock_adapter_is_removed():
    conftest = Path(__file__).with_name("conftest.py").read_text(encoding="utf-8")
    official_tests = Path(__file__).with_name("test_official.py").read_text(encoding="utf-8")

    assert "adapt_legacy_official_playwright_mocks" not in conftest
    assert "page.on = _on" not in official_tests
    assert "_fire_response_events" not in official_tests
    assert "page.expect_response" in official_tests
