import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

import scraper
from scrapers.official import parse_ctbc_api, scrape_ctbc_playwright


ETF_CODE = "00406A"
SOURCE_URL = "https://www.ctbcinvestments.com/Etf/00682450/Combination"
API_URL = "https://www.ctbcinvestments.com.tw/API/etf/ETFHoldingWeight?token=fake"
DATA_DATE = "2026/07/16"


def _stock_items():
    return [
        {"code_": code, "name_": name, "qty_": "100,000.00", "weights_": "20.00"}
        for code, name in [
            ("2330", "台積電"),
            ("2308", "台達電"),
            ("2454", "聯發科"),
            ("2317", "鴻海"),
            ("2382", "廣達"),
        ]
    ]


def _payload(*, include_date=True, include_non_stock=False):
    groups = [
        {"Code": "STOCK", "Name": "股票", "Data": _stock_items()},
    ]
    if include_non_stock:
        groups.append(
            {
                "Code": "CASH",
                "Name": "現金",
                "Data": [
                    {
                        "code_": "1234",
                        "name_": "新台幣現金",
                        "qty_": "1,000.00",
                        "weights_": "5.00",
                    }
                ],
            }
        )

    data = {"FundAssetsDetail": groups}
    if include_date:
        data["FundAssets"] = [{"資料日期": DATA_DATE}]
    return json.dumps({"Data": data})


class _Response:
    def __init__(self, body):
        self.url = API_URL
        self._body = body

    async def text(self):
        return self._body


def _page_that_fires(body):
    page = AsyncMock()
    callbacks = {}

    def on(event, callback):
        callbacks[event] = callback

    async def goto(*args, **kwargs):
        await callbacks["response"](_Response(body))

    page.on = Mock(side_effect=on)
    page.goto = AsyncMock(side_effect=goto)
    page.wait_for_event = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.remove_listener = Mock()
    return page


def _page_that_times_out():
    page = AsyncMock()
    page.on = Mock()
    page.goto = AsyncMock()
    page.wait_for_event = AsyncMock(side_effect=TimeoutError("timed out"))
    page.wait_for_timeout = AsyncMock()
    page.remove_listener = Mock()
    return page


def _official_result(data_date=DATA_DATE):
    row = {
        "date": data_date,
        "etf_code": ETF_CODE,
        "asset_name": "台積電(2330.TW)",
        "asset_type": "stock",
        "stock_code": "2330",
        "stock_name": "台積電",
        "shares": 100000,
        "weight_pct": 10.0,
        "source_url": SOURCE_URL,
        "source_type": "official_fallback",
        "extraction_method": "playwright_api_intercept",
    }
    return {
        "ok": True,
        "reason": "ok",
        "all_rows": [row],
        "stock_rows": [row],
        "non_stock_rows": [],
        "source_url": SOURCE_URL,
        "source_type": "official_fallback",
        "total_weight_all_rows": 10.0,
        "total_weight_stock_rows": 10.0,
    }


@pytest.mark.asyncio
async def test_browser_method_reaches_official_browser_scraper():
    page = object()
    browser_scraper = AsyncMock(return_value=_official_result())
    static_scraper = Mock()

    with patch(
        "scraper.get_etf_config",
        return_value={"official_method": "browser", "issuer": "CTBC"},
    ), patch(
        "scraper.scrape_official_with_browser",
        new=browser_scraper,
    ), patch(
        "scraper._official_fallback_static",
        new=static_scraper,
    ):
        result = await scraper._official_fallback_with_browser(ETF_CODE, page)

    browser_scraper.assert_awaited_once_with(ETF_CODE, page)
    static_scraper.assert_not_called()
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_undated_ctbc_browser_result_falls_through_to_static():
    page = object()
    browser_scraper = AsyncMock(return_value=_official_result(data_date=None))
    static_result = _official_result()
    static_scraper = Mock(return_value=static_result)

    with patch(
        "scraper.get_etf_config",
        return_value={"official_method": "browser", "issuer": "CTBC"},
    ), patch(
        "scraper.scrape_official_with_browser",
        new=browser_scraper,
    ), patch(
        "scraper._official_fallback_static",
        new=static_scraper,
    ):
        result = await scraper._official_fallback_with_browser(ETF_CODE, page)

    browser_scraper.assert_awaited_once_with(ETF_CODE, page)
    static_scraper.assert_called_once_with(ETF_CODE)
    assert result == static_result


@pytest.mark.asyncio
async def test_unsupported_browser_issuer_still_falls_through_to_static():
    page = object()
    browser_failure = {**scraper.FAILED_RESULT, "reason": "unsupported"}
    browser_scraper = AsyncMock(return_value=browser_failure)
    static_result = _official_result()
    static_scraper = Mock(return_value=static_result)

    with patch(
        "scraper.get_etf_config",
        return_value={"official_method": "browser", "issuer": "Cathay"},
    ), patch(
        "scraper.scrape_official_with_browser",
        new=browser_scraper,
    ), patch(
        "scraper._official_fallback_static",
        new=static_scraper,
    ):
        result = await scraper._official_fallback_with_browser(ETF_CODE, page)

    browser_scraper.assert_awaited_once_with(ETF_CODE, page)
    static_scraper.assert_called_once_with(ETF_CODE)
    assert result == static_result


def test_ctbc_parser_ignores_non_stock_groups():
    rows = parse_ctbc_api(
        _payload(include_non_stock=True),
        ETF_CODE,
        SOURCE_URL,
    )

    assert [row["stock_code"] for row in rows] == [
        "2330",
        "2308",
        "2454",
        "2317",
        "2382",
    ]


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_ctbc_scraper_returns_one_valid_date_for_all_rows(mock_config):
    mock_config.return_value = {
        "url": SOURCE_URL,
        "method": "browser",
        "issuer": "CTBC",
    }
    page = _page_that_fires(_payload())

    result = await scrape_ctbc_playwright(ETF_CODE, page)

    assert result["ok"] is True
    assert {row["date"] for row in result["all_rows"]} == {DATA_DATE}


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_ctbc_timeout_does_not_add_fixed_polling_delay(mock_config):
    mock_config.return_value = {
        "url": SOURCE_URL,
        "method": "browser",
        "issuer": "CTBC",
    }
    page = _page_that_times_out()

    result = await scrape_ctbc_playwright(ETF_CODE, page)

    assert result["ok"] is False
    page.wait_for_timeout.assert_not_awaited()
