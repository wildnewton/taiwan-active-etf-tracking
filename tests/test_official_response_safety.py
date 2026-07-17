import json
from unittest.mock import AsyncMock, patch

import pytest
from playwright.async_api import Error as PlaywrightError

from scrapers.official import (
    _is_ctbc_holdings_response,
    _is_nomura_assets_response,
    parse_ctbc_api,
    scrape_capital_playwright,
    scrape_ctbc_playwright,
    scrape_nomura_stealth,
    scrape_official_with_browser,
)


CAPITAL_PAGE_URL = "https://www.capitalfund.com.tw/etf/product/detail/399/buyback"
CAPITAL_API_URL = "https://www.capitalfund.com.tw/CFWeb/api/etf/buyback"
NOMURA_PAGE_URL = (
    "https://www.nomurafunds.com.tw/ETFWEB/product-description"
    "?fundNo=00980A&tab=Shareholding"
)
NOMURA_API_URL = "https://www.nomurafunds.com.tw/API/ETFAPI/api/Fund/GetFundAssets"
CTBC_PAGE_URL = "https://www.ctbcinvestments.com/Etf/00682450/Combination"
CTBC_API_URL = "https://www.ctbcinvestments.com.tw/API/etf/ETFHoldingWeight?token=fake"
DATA_DATE = "2026/07/16"


_STOCKS = [
    ("2330", "台積電"),
    ("2308", "台達電"),
    ("2454", "聯發科"),
    ("2317", "鴻海"),
    ("2382", "廣達"),
]


def _capital_payload():
    return json.dumps(
        {
            "data": {
                "pcf": {"date2": DATA_DATE},
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


def _nomura_payload(*, fund_id="00980A"):
    return json.dumps(
        {
            "Entries": {
                "FundID": fund_id,
                "Data": {
                    "FundAsset": {"NavDate": DATA_DATE},
                    "Table": [
                        {
                            "TableTitle": "股票",
                            "Rows": [
                                [code, name, "100000", "20.0"]
                                for code, name in _STOCKS
                            ],
                        }
                    ],
                },
            }
        }
    )


def _ctbc_payload():
    return json.dumps(
        {
            "Data": {
                "FundAssets": [{"資料日期": DATA_DATE}],
                "FundAssetsDetail": [
                    {
                        "Code": "STOCK",
                        "Data": [
                            {
                                "code_": code,
                                "name_": name,
                                "qty_": "100,000.00",
                                "weights_": "20.00",
                            }
                            for code, name in _STOCKS
                        ],
                    }
                ],
            }
        }
    )


class _Request:
    def __init__(self, method="GET"):
        self.method = method


class _Response:
    def __init__(self, url, body="{}", *, ok=True, method="GET"):
        self.url = url
        self.ok = ok
        self.request = _Request(method)
        self._body = body

    async def text(self):
        return self._body


class _ResponseInfo:
    def __init__(self, response):
        self._response = response

    @property
    def value(self):
        async def resolve():
            return self._response

        return resolve()


class _ResponseErrorContext:
    def __init__(self, response, error):
        self._response_info = _ResponseInfo(response)
        self._error = error

    async def __aenter__(self):
        return self._response_info

    async def __aexit__(self, exc_type, exc, traceback):
        if exc_type is None:
            raise self._error
        return False


class _ResponsePage:
    def __init__(self, response, error=None):
        self.response = response
        self.error = error
        self.goto = AsyncMock()

    def expect_response(self, predicate, timeout):
        assert timeout <= 10000
        assert predicate(self.response)
        return _ResponseErrorContext(self.response, self.error)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scraper_fn", "etf_code", "config", "response"),
    [
        (
            scrape_capital_playwright,
            "00982A",
            {"url": CAPITAL_PAGE_URL, "method": "api", "issuer": "Capital"},
            _Response(CAPITAL_API_URL, _capital_payload()),
        ),
        (
            scrape_nomura_stealth,
            "00980A",
            {"url": NOMURA_PAGE_URL, "method": "stealth_api", "issuer": "Nomura"},
            _Response(NOMURA_API_URL, _nomura_payload()),
        ),
        (
            scrape_ctbc_playwright,
            "00406A",
            {"url": CTBC_PAGE_URL, "method": "browser", "issuer": "CTBC"},
            _Response(CTBC_API_URL, _ctbc_payload()),
        ),
    ],
)
async def test_response_wait_playwright_error_after_navigation_returns_failure(
    scraper_fn,
    etf_code,
    config,
    response,
):
    page = _ResponsePage(response, PlaywrightError("page closed"))

    with patch("scrapers.official.get_official_config", return_value=config):
        result = await scraper_fn(etf_code, page)

    assert result["ok"] is False
    assert "not intercepted" in result["reason"]
    page.goto.assert_awaited_once()


@pytest.mark.asyncio
async def test_ctbc_browser_dispatch_uses_real_dispatcher_branch():
    page = object()
    handler_result = {"ok": False, "reason": "sentinel"}

    with patch(
        "scrapers.official.get_official_config",
        return_value={"url": CTBC_PAGE_URL, "method": "browser", "issuer": "CTBC"},
    ), patch(
        "scrapers.official.scrape_ctbc_playwright",
        new=AsyncMock(return_value=handler_result),
    ) as handler:
        result = await scrape_official_with_browser("00406A", page)

    handler.assert_awaited_once_with("00406A", page)
    assert result is handler_result


def test_ctbc_parser_preserves_exact_holding_values():
    rows = parse_ctbc_api(_ctbc_payload(), "00406A", CTBC_PAGE_URL)

    assert rows[0] == {
        "date": DATA_DATE,
        "etf_code": "00406A",
        "asset_name": "台積電(2330.TW)",
        "asset_type": "stock",
        "stock_code": "2330",
        "stock_name": "台積電",
        "shares": 100000.0,
        "weight_pct": 20.0,
        "source_url": CTBC_PAGE_URL,
        "source_type": "official_fallback",
        "extraction_method": "playwright_api_intercept",
    }
    assert sum(row["weight_pct"] for row in rows) == 100.0


@pytest.mark.parametrize(
    ("predicate", "valid_url", "wrong_url"),
    [
        (
            _is_nomura_assets_response,
            NOMURA_API_URL,
            "https://evil.example/API/ETFAPI/api/Fund/GetFundAssets",
        ),
        (
            _is_ctbc_holdings_response,
            CTBC_API_URL,
            "https://evil.example/API/etf/ETFHoldingWeight",
        ),
    ],
)
def test_api_response_predicates_require_expected_endpoint(predicate, valid_url, wrong_url):
    assert predicate(_Response(valid_url)) is True
    assert predicate(_Response(wrong_url)) is False
    assert predicate(_Response(valid_url, ok=False)) is False
    assert predicate(_Response(valid_url, method="POST")) is False


@pytest.mark.asyncio
async def test_nomura_rejects_payload_for_different_fund():
    page = _ResponsePage(_Response(NOMURA_API_URL, _nomura_payload(fund_id="00985A")))
    config = {"url": NOMURA_PAGE_URL, "method": "stealth_api", "issuer": "Nomura"}

    with patch("scrapers.official.get_official_config", return_value=config):
        result = await scrape_nomura_stealth("00980A", page)

    assert result["ok"] is False
    assert "fund" in result["reason"].lower()
