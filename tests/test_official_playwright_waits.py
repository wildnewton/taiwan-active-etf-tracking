import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from scrapers.official import (
    _is_nomura_assets_response,
    scrape_capital_playwright,
    scrape_mega_playwright,
    scrape_nomura_stealth,
    scrape_uni_president_playwright,
)


CAPITAL_URL = "https://www.capitalfund.com.tw/etf/product/detail/399/buyback"
NOMURA_URL = (
    "https://www.nomurafunds.com.tw/ETFWEB/product-description"
    "?fundNo=00980A&tab=Shareholding"
)
MEGA_URL = "https://www.megafunds.com.tw/MEGA/etf/etf_product.aspx?id=23"
UNI_PRESIDENT_URL = "https://www.ezmoney.com.tw/ETF/Fund/Info?fundCode=49YTW"


_CAPITAL_STOCKS = [
    {"stocNo": "2330", "stocName": "台積電", "share": 800000, "weightRound": 12.30},
    {"stocNo": "2317", "stocName": "鴻海", "share": 500000, "weightRound": 8.20},
    {"stocNo": "2382", "stocName": "廣達", "share": 210000, "weightRound": 6.10},
    {"stocNo": "2308", "stocName": "台達電", "share": 300000, "weightRound": 5.50},
    {"stocNo": "2454", "stocName": "聯發科", "share": 180000, "weightRound": 4.80},
    {"stocNo": "2881", "stocName": "富邦金", "share": 400000, "weightRound": 4.50},
    {"stocNo": "2882", "stocName": "國泰金", "share": 350000, "weightRound": 4.20},
    {"stocNo": "2891", "stocName": "中信金", "share": 500000, "weightRound": 4.00},
    {"stocNo": "3711", "stocName": "日月光投控", "share": 200000, "weightRound": 3.80},
    {"stocNo": "2412", "stocName": "中華電", "share": 250000, "weightRound": 3.50},
    {"stocNo": "3034", "stocName": "聯詠", "share": 150000, "weightRound": 3.20},
    {"stocNo": "2395", "stocName": "研華", "share": 120000, "weightRound": 3.00},
    {"stocNo": "3008", "stocName": "大立光", "share": 50000, "weightRound": 2.80},
    {"stocNo": "2002", "stocName": "中鋼", "share": 600000, "weightRound": 2.60},
    {"stocNo": "1301", "stocName": "台塑", "share": 300000, "weightRound": 2.50},
    {"stocNo": "1303", "stocName": "南亞", "share": 280000, "weightRound": 2.40},
    {"stocNo": "3045", "stocName": "台灣大", "share": 200000, "weightRound": 2.30},
    {"stocNo": "6505", "stocName": "台塑化", "share": 250000, "weightRound": 2.20},
    {"stocNo": "5880", "stocName": "合庫金", "share": 400000, "weightRound": 2.10},
    {"stocNo": "5871", "stocName": "中租-KY", "share": 100000, "weightRound": 2.00},
]
CAPITAL_API_JSON = json.dumps(
    {"data": {"pcf": {"date2": "2026-06-18"}, "stocks": _CAPITAL_STOCKS}}
)

_NOMURA_ROWS = [
    ["2330", "台灣積體電路製造", "704000", "9.58"],
    ["2308", "台達電子工業", "475000", "5.54"],
    ["2454", "聯發科技", "188000", "4.55"],
    ["2317", "鴻海精密工業", "500000", "5.20"],
    ["2382", "廣達電腦", "300000", "4.80"],
    ["2881", "富邦金融控股", "400000", "4.30"],
    ["2882", "國泰金融控股", "350000", "4.10"],
    ["2891", "中國信託金融控股", "500000", "3.90"],
    ["3711", "日月光投控", "200000", "3.50"],
    ["2412", "中華電信", "250000", "3.30"],
    ["3034", "聯詠科技", "150000", "3.10"],
    ["2395", "研華科技", "120000", "2.90"],
    ["3008", "大立光電", "50000", "2.70"],
    ["2002", "中國鋼鐵", "600000", "2.50"],
    ["1301", "台灣塑膠工業", "300000", "2.40"],
    ["1303", "南亞塑膠工業", "280000", "2.30"],
    ["3045", "台灣大哥大", "200000", "2.20"],
    ["6505", "台塑石化", "250000", "2.10"],
    ["5880", "合作金庫金融控股", "400000", "2.00"],
    ["5871", "中租控股", "100000", "1.90"],
]
NOMURA_API_JSON = json.dumps(
    {
        "Entries": {
            "Data": {
                "FundAsset": {"NavDate": "2026-06-22"},
                "Table": [{"TableTitle": "股票", "Rows": _NOMURA_ROWS}],
            }
        }
    }
)


class _Response:
    def __init__(self, url, body):
        self.url = url
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


class _ExpectResponseContext:
    def __init__(self, response, order, exit_error=None):
        self._response_info = _ResponseInfo(response)
        self._order = order
        self._exit_error = exit_error

    async def __aenter__(self):
        self._order.append("response_wait_registered")
        return self._response_info

    async def __aexit__(self, exc_type, exc, traceback):
        if exc_type is None and self._exit_error is not None:
            raise self._exit_error
        return False


class _Timeout(Exception):
    pass


def _mock_page(
    response_url=None,
    response_body=None,
    body_text="",
    response_wait_error=None,
):
    page = AsyncMock()
    order = []

    async def goto(*args, **kwargs):
        order.append("goto")

    page.goto = AsyncMock(side_effect=goto)
    page.wait_for_timeout = AsyncMock()
    page.wait_for_event = AsyncMock(side_effect=AssertionError("late response wait used"))
    page.wait_for_selector = AsyncMock()
    page.on = Mock()
    page.remove_listener = Mock()
    page.locator = Mock()
    page.locator.return_value.inner_text = AsyncMock(return_value=body_text)
    page.query_selector_all = AsyncMock(return_value=[])
    page._order = order

    response = _Response(response_url or "", response_body or "{}")

    def expect_response(predicate, timeout):
        assert timeout <= 10000
        if response_url:
            assert predicate(response)
        return _ExpectResponseContext(response, order, response_wait_error)

    page.expect_response = Mock(side_effect=expect_response)
    return page


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_capital_registers_bounded_response_wait_before_navigation(mock_config):
    mock_config.return_value = {
        "url": CAPITAL_URL,
        "method": "api",
        "issuer": "Capital",
        "official_logic": "buyback",
    }
    page = _mock_page(
        response_url="https://www.capitalfund.com.tw/CFWeb/api/etf/buyback",
        response_body=CAPITAL_API_JSON,
    )

    result = await scrape_capital_playwright("00982A", page)

    assert result["ok"] is True
    assert page._order == ["response_wait_registered", "goto"]
    page.expect_response.assert_called_once()
    page.wait_for_event.assert_not_awaited()
    page.wait_for_timeout.assert_not_called()
    page.on.assert_not_called()


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_capital_navigation_error_still_propagates(mock_config):
    mock_config.return_value = {
        "url": CAPITAL_URL,
        "method": "api",
        "issuer": "Capital",
        "official_logic": "buyback",
    }
    page = _mock_page(
        response_url="https://www.capitalfund.com.tw/CFWeb/api/etf/buyback",
        response_body=CAPITAL_API_JSON,
    )
    page.goto.side_effect = RuntimeError("navigation failed")

    with pytest.raises(RuntimeError, match="navigation failed"):
        await scrape_capital_playwright("00982A", page)

    page.expect_response.assert_called_once()
    page.on.assert_not_called()


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_capital_navigation_timeout_still_propagates(mock_config):
    mock_config.return_value = {
        "url": CAPITAL_URL,
        "method": "api",
        "issuer": "Capital",
        "official_logic": "buyback",
    }
    page = _mock_page(
        response_url="https://www.capitalfund.com.tw/CFWeb/api/etf/buyback",
        response_body=CAPITAL_API_JSON,
    )
    page.goto.side_effect = PlaywrightTimeoutError("navigation timed out")

    with pytest.raises(PlaywrightTimeoutError, match="navigation timed out"):
        await scrape_capital_playwright("00982A", page)


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_capital_response_timeout_returns_clean_failure(mock_config):
    mock_config.return_value = {
        "url": CAPITAL_URL,
        "method": "api",
        "issuer": "Capital",
        "official_logic": "buyback",
    }
    page = _mock_page(response_wait_error=PlaywrightTimeoutError("timed out"))

    result = await scrape_capital_playwright("00982A", page)

    assert result["ok"] is False
    assert "not intercepted" in result["reason"]
    page.expect_response.assert_called_once()
    page.wait_for_event.assert_not_awaited()


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_nomura_registers_bounded_response_wait_before_navigation(mock_config):
    mock_config.return_value = {
        "url": NOMURA_URL,
        "method": "stealth_api",
        "issuer": "Nomura",
        "official_logic": "GetFundAssets",
    }
    page = _mock_page(
        response_url="https://www.nomurafunds.com.tw/API/ETFAPI/api/Fund/GetFundAssets",
        response_body=NOMURA_API_JSON,
    )

    result = await scrape_nomura_stealth("00980A", page)

    assert result["ok"] is True
    assert page._order == ["response_wait_registered", "goto"]
    page.expect_response.assert_called_once()
    page.wait_for_event.assert_not_awaited()
    page.wait_for_timeout.assert_not_called()
    page.on.assert_not_called()


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_nomura_navigation_error_still_propagates(mock_config):
    mock_config.return_value = {
        "url": NOMURA_URL,
        "method": "stealth_api",
        "issuer": "Nomura",
        "official_logic": "GetFundAssets",
    }
    page = _mock_page(
        response_url="https://www.nomurafunds.com.tw/API/ETFAPI/api/Fund/GetFundAssets",
        response_body=NOMURA_API_JSON,
    )
    page.goto.side_effect = RuntimeError("navigation failed")

    with pytest.raises(RuntimeError, match="navigation failed"):
        await scrape_nomura_stealth("00980A", page)

    page.expect_response.assert_called_once()
    page.on.assert_not_called()


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_nomura_navigation_timeout_still_propagates(mock_config):
    mock_config.return_value = {
        "url": NOMURA_URL,
        "method": "stealth_api",
        "issuer": "Nomura",
        "official_logic": "GetFundAssets",
    }
    page = _mock_page(
        response_url="https://www.nomurafunds.com.tw/API/ETFAPI/api/Fund/GetFundAssets",
        response_body=NOMURA_API_JSON,
    )
    page.goto.side_effect = PlaywrightTimeoutError("navigation timed out")

    with pytest.raises(PlaywrightTimeoutError, match="navigation timed out"):
        await scrape_nomura_stealth("00980A", page)


def test_nomura_assets_response_predicate_is_case_insensitive():
    response = _Response("https://www.nomurafunds.com.tw/API/ETFAPI/api/Fund/GetFundAssets", "{}")

    assert _is_nomura_assets_response(response) is True


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_mega_playwright_does_not_use_networkidle(mock_config):
    mock_config.return_value = {
        "url": MEGA_URL,
        "method": "playwright",
        "issuer": "Mega",
        "official_logic": "text_parse",
    }
    page = _mock_page(body_text="")
    goto_kwargs = {}

    async def goto_side_effect(*args, **kwargs):
        nonlocal goto_kwargs
        goto_kwargs = kwargs

    page.goto.side_effect = goto_side_effect

    await scrape_mega_playwright("00996A", page)

    assert goto_kwargs.get("wait_until") != "networkidle"


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_uni_president_playwright_does_not_use_networkidle(mock_config):
    mock_config.return_value = {
        "url": UNI_PRESIDENT_URL,
        "method": "playwright",
        "issuer": "Uni-President",
        "official_logic": "table_parse",
    }
    page = _mock_page()
    goto_kwargs = {}

    async def goto_side_effect(*args, **kwargs):
        nonlocal goto_kwargs
        goto_kwargs = kwargs

    page.goto.side_effect = goto_side_effect

    await scrape_uni_president_playwright("00981A", page)

    assert goto_kwargs.get("wait_until") != "networkidle"


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_uni_president_waits_for_table_after_load(mock_config):
    mock_config.return_value = {
        "url": UNI_PRESIDENT_URL,
        "method": "playwright",
        "issuer": "Uni-President",
        "official_logic": "table_parse",
    }
    page = _mock_page()

    await scrape_uni_president_playwright("00981A", page)

    page.wait_for_selector.assert_awaited_once_with("table", timeout=10000)


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_uni_president_table_wait_timeout_returns_clean_failure(mock_config):
    mock_config.return_value = {
        "url": UNI_PRESIDENT_URL,
        "method": "playwright",
        "issuer": "Uni-President",
        "official_logic": "table_parse",
    }
    page = _mock_page()
    page.wait_for_selector.side_effect = _Timeout("table timeout")

    result = await scrape_uni_president_playwright("00981A", page)

    assert result["ok"] is False
    assert "holdings table not found" in result["reason"]


def test_official_scraper_has_no_runtime_networkidle_waits():
    official_py = Path(__file__).resolve().parent.parent / "scripts" / "scrapers" / "official.py"

    assert 'wait_until="networkidle"' not in official_py.read_text(encoding="utf-8")
