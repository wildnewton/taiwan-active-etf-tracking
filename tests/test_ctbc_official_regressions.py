import json
from unittest.mock import AsyncMock, Mock, patch

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

import scraper
from scrapers.official import (
    parse_ctbc_api,
    scrape_ctbc_playwright,
    scrape_official_with_browser,
)


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


def _payload(*, include_date=True, include_non_stock=False, stock_items=None):
    groups = [
        {
            "Code": "STOCK",
            "Name": "股票",
            "Data": _stock_items() if stock_items is None else stock_items,
        },
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


def _page_with_response(body, response_wait_error=None):
    page = AsyncMock()
    order = []
    response = _Response(body)

    async def goto(*args, **kwargs):
        order.append("goto")

    def expect_response(predicate, timeout):
        assert timeout <= 10000
        assert predicate(response)
        return _ExpectResponseContext(response, order, response_wait_error)

    page.goto = AsyncMock(side_effect=goto)
    page.expect_response = Mock(side_effect=expect_response)
    page.wait_for_event = AsyncMock(side_effect=AssertionError("late response wait used"))
    page.wait_for_timeout = AsyncMock()
    page.on = Mock()
    page.remove_listener = Mock()
    page._order = order
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
async def test_undated_ctbc_result_falls_through_to_static():
    page = _page_with_response(_payload(include_date=False))
    static_result = _official_result()
    static_scraper = Mock(return_value=static_result)
    config = {
        "code": ETF_CODE,
        "name": "主動中信台灣收益",
        "url": SOURCE_URL,
        "method": "browser",
        "issuer": "CTBC",
        "official_method": "browser",
    }

    with patch("scraper.get_etf_config", return_value=config), patch(
        "scrapers.official.get_official_config",
        return_value=config,
    ), patch(
        "scraper._official_fallback_static",
        new=static_scraper,
    ):
        result = await scraper._official_fallback_with_browser(ETF_CODE, page)

    page.expect_response.assert_called_once()
    static_scraper.assert_called_once_with(ETF_CODE)
    assert result == static_result


@pytest.mark.asyncio
async def test_unsupported_browser_dispatcher_does_not_navigate():
    page = AsyncMock()
    page.goto = AsyncMock()

    with patch(
        "scrapers.official.get_official_config",
        return_value={
            "url": "https://example.com/unsupported",
            "method": "browser",
            "issuer": "Cathay",
        },
    ):
        result = await scrape_official_with_browser(ETF_CODE, page)

    assert result["ok"] is False
    assert "No browser official scraper" in result["reason"]
    page.goto.assert_not_awaited()


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
async def test_ctbc_scraper_registers_response_wait_before_navigation(mock_config):
    mock_config.return_value = {
        "url": SOURCE_URL,
        "method": "browser",
        "issuer": "CTBC",
    }
    page = _page_with_response(_payload())

    result = await scrape_ctbc_playwright(ETF_CODE, page)

    assert result["ok"] is True
    assert {row["date"] for row in result["all_rows"]} == {DATA_DATE}
    assert page._order == ["response_wait_registered", "goto"]
    page.expect_response.assert_called_once()
    page.wait_for_event.assert_not_awaited()
    page.on.assert_not_called()


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_ctbc_scraper_rejects_missing_source_date(mock_config):
    mock_config.return_value = {
        "url": SOURCE_URL,
        "method": "browser",
        "issuer": "CTBC",
    }
    page = _page_with_response(_payload(include_date=False))

    result = await scrape_ctbc_playwright(ETF_CODE, page)

    assert result["ok"] is False
    assert "date" in result["reason"].lower()


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_ctbc_empty_stock_group_preserves_empty_rows_reason(mock_config):
    mock_config.return_value = {
        "url": SOURCE_URL,
        "method": "browser",
        "issuer": "CTBC",
    }
    page = _page_with_response(_payload(stock_items=[]))

    result = await scrape_ctbc_playwright(ETF_CODE, page)

    assert result["ok"] is False
    assert result["reason"] == "empty rows"


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_ctbc_timeout_does_not_add_fixed_polling_delay(mock_config):
    mock_config.return_value = {
        "url": SOURCE_URL,
        "method": "browser",
        "issuer": "CTBC",
    }
    page = _page_with_response(
        _payload(),
        response_wait_error=PlaywrightTimeoutError("timed out"),
    )

    result = await scrape_ctbc_playwright(ETF_CODE, page)

    assert result["ok"] is False
    page.expect_response.assert_called_once()
    page.wait_for_event.assert_not_awaited()
    page.wait_for_timeout.assert_not_awaited()


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_ctbc_navigation_error_still_propagates(mock_config):
    mock_config.return_value = {
        "url": SOURCE_URL,
        "method": "browser",
        "issuer": "CTBC",
    }
    page = _page_with_response(_payload())
    page.goto.side_effect = RuntimeError("navigation failed")

    with pytest.raises(RuntimeError, match="navigation failed"):
        await scrape_ctbc_playwright(ETF_CODE, page)

    page.expect_response.assert_called_once()
    page.on.assert_not_called()


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_ctbc_navigation_timeout_still_propagates(mock_config):
    mock_config.return_value = {
        "url": SOURCE_URL,
        "method": "browser",
        "issuer": "CTBC",
    }
    page = _page_with_response(_payload())
    page.goto.side_effect = PlaywrightTimeoutError("navigation timed out")

    with pytest.raises(PlaywrightTimeoutError, match="navigation timed out"):
        await scrape_ctbc_playwright(ETF_CODE, page)
