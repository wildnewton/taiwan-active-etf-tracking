import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from scrapers.official import (
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
CAPITAL_API_JSON = json.dumps({"data": {"pcf": {"date2": "2026-06-18"}, "stocks": _CAPITAL_STOCKS}})


class _Response:
    def __init__(self, url, body):
        self.url = url
        self._body = body

    async def text(self):
        return self._body


class _Timeout(Exception):
    pass


def _mock_page(response_url=None, response_body=None, body_text=""):
    page = AsyncMock()
    page.goto = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.wait_for_response = AsyncMock()
    page.remove_listener = Mock()

    callbacks = {}

    def on(event, callback):
        callbacks[event] = callback

    page.on = on
    page._callbacks = callbacks
    page.locator = Mock()
    page.locator.return_value.inner_text = AsyncMock(return_value=body_text)
    page.query_selector_all = AsyncMock(return_value=[])

    if response_url:
        response = _Response(response_url, response_body or "{}")

        async def wait_for_response(predicate, timeout):
            assert timeout <= 10000
            assert predicate(response)
            callback = callbacks.get("response")
            if callback:
                await callback(response)
            return response

        page.wait_for_response.side_effect = wait_for_response

    return page


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_capital_waits_for_bounded_buyback_response_instead_of_fixed_sleep(mock_config):
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
    page.wait_for_response.assert_awaited_once()
    page.wait_for_timeout.assert_not_called()


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_capital_removes_response_listener_when_navigation_raises(mock_config):
    mock_config.return_value = {
        "url": CAPITAL_URL,
        "method": "api",
        "issuer": "Capital",
        "official_logic": "buyback",
    }
    page = _mock_page()
    page.goto.side_effect = RuntimeError("navigation failed")

    with pytest.raises(RuntimeError, match="navigation failed"):
        await scrape_capital_playwright("00982A", page)

    assert page.remove_listener.called


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_capital_still_fails_cleanly_when_buyback_response_times_out(mock_config):
    mock_config.return_value = {
        "url": CAPITAL_URL,
        "method": "api",
        "issuer": "Capital",
        "official_logic": "buyback",
    }
    page = _mock_page()
    page.wait_for_response.side_effect = _Timeout("timed out")

    result = await scrape_capital_playwright("00982A", page)

    assert result["ok"] is False
    assert "not intercepted" in result["reason"]
    assert page.remove_listener.called


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_nomura_removes_response_listener_when_navigation_raises(mock_config):
    mock_config.return_value = {
        "url": NOMURA_URL,
        "method": "stealth_api",
        "issuer": "Nomura",
        "official_logic": "GetFundAssets",
    }
    page = _mock_page()
    page.goto.side_effect = RuntimeError("navigation failed")

    with pytest.raises(RuntimeError, match="navigation failed"):
        await scrape_nomura_stealth("00980A", page)

    assert page.remove_listener.called


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


def test_official_scraper_has_no_runtime_networkidle_waits():
    official_py = Path(__file__).resolve().parent.parent / "scripts" / "scrapers" / "official.py"

    assert 'wait_until="networkidle"' not in official_py.read_text(encoding="utf-8")
