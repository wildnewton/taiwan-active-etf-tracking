from unittest.mock import AsyncMock, Mock, patch

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from scrapers.official import (
    _extract_jpmorgan_holdings_date,
    _parse_jpmorgan_cash_rows,
    _parse_jpmorgan_derivative_rows,
    _parse_jpmorgan_stock_rows,
    scrape_jpmorgan_playwright,
    scrape_official_with_browser,
)


JPMORGAN_URL = (
    "https://am.jpmorgan.com/tw/zh/asset-management/twetf/products/"
    "jpmorgan-taiwan-taiwan-equity-high-income-active-etf-"
    "tw00000401a1#/portfolio"
)

STOCK_TABLE_ROWS = [
    ["股票代碼", "股票名稱", "股數", "金額", "權重(%)"],
    ["2330", "台灣積體電路製造", "110,000", "264,000,000", "8.66%"],
    ["2454", "聯發科技", "57,600", "221,760,000", "7.27%"],
    ["2308", "台達電子工業", "68,000", "127,840,000", "4.19%"],
    ["7769", "鴻勁", "13,000", "83,265,000", "2.73%"],
    ["2345", "智邦科技", "36,053", "82,381,105", "2.70%"],
    ["3711", "日月光", "123,000", "80,688,000", "2.65%"],
    ["6669", "緯穎", "14,000", "77,000,000", "2.52%"],
    ["4958", "臻鼎-KY", "135,000", "75,465,000", "2.47%"],
    ["6223", "旺矽科技", "12,074", "72,444,000", "2.38%"],
    ["2382", "廣達電腦", "193,000", "63,497,000", "2.08%"],
]

FUTURE_ROWS = [
    ["商品代碼", "商品名稱", "商品數量 (口數)", "權重 (%)"],
    ["FTQ6", "臺股期貨08/26", "24", "-0.18%"],
]

OPTION_ROWS = [
    ["商品代碼", "商品名稱", "商品數量 (口數)", "權重 (%)"],
    ["TWSE 08/19/26 C45600", "臺指選擇權08/26 45600 買權", "-310", "-0.61%"],
]

CASH_ROWS = [
    ["名稱", "金額 (TWD)", "權重 (%)"],
    ["NEW TAIWAN DOLLAR", "283,733,974", "9.30%"],
]


def _config():
    return {
        "url": JPMORGAN_URL,
        "method": "playwright",
        "issuer": "JPMorgan",
        "official_logic": "slug=jpmorgan-tw-equity-high-income-etf",
        "internal_id": None,
        "internal_ids": {},
        "code": "00401A",
        "name": "主動摩根台灣鑫收",
    }


def _mock_page(body_text="截至 2026/07/22\n基金資產 - 股票\n共 10 筆"):
    page = AsyncMock()
    page.goto = AsyncMock()
    page.wait_for_selector = AsyncMock()
    page.wait_for_timeout = AsyncMock()

    locator = AsyncMock()
    locator.inner_text = AsyncMock(return_value=body_text)
    page.locator = Mock(return_value=locator)
    page.query_selector_all = AsyncMock(return_value=[])
    return page


def _mock_table(data_rows):
    rows = []
    for row_data in data_rows:
        row = AsyncMock()
        cells = []
        for value in row_data:
            cell = AsyncMock()
            cell.inner_text = AsyncMock(return_value=value)
            cells.append(cell)
        row.query_selector_all = AsyncMock(return_value=cells)
        row.inner_text = AsyncMock(return_value="\t".join(row_data))
        rows.append(row)

    table = AsyncMock()
    table.query_selector_all = AsyncMock(return_value=rows)
    table.query_selector = AsyncMock(return_value=rows[0])
    table.is_visible = AsyncMock(return_value=True)

    async def evaluate(script, arg=None):
        if "view-50" in script:
            return True
        if "pagination-input" in script and "total-pages" in script:
            return {"current_page": 1, "total_pages": 1}
        if "right-chevron" in script:
            return False
        raise AssertionError(f"unexpected evaluate script: {script}")

    table.evaluate = AsyncMock(side_effect=evaluate)
    return table


def _set_tables(page, stock_rows=STOCK_TABLE_ROWS):
    page.query_selector_all = AsyncMock(
        return_value=[
            _mock_table(stock_rows),
            _mock_table(FUTURE_ROWS),
            _mock_table(OPTION_ROWS),
            _mock_table(CASH_ROWS),
        ]
    )


def _generated_stock_rows(count):
    rows = [["股票代碼", "股票名稱", "股數", "金額", "權重(%)"]]
    for offset in range(count):
        rows.append(
            [
                str(1000 + offset),
                f"測試股票{offset + 1}",
                "1,000",
                "1,000,000",
                "1.50%",
            ]
        )
    return rows


@patch("scrapers.official.get_official_config", return_value=_config())
@pytest.mark.asyncio
async def test_jpmorgan_complete_snapshot_succeeds(mock_config):
    page = _mock_page()
    _set_tables(page)

    result = await scrape_jpmorgan_playwright("00401A", page)

    assert result["ok"] is True
    assert len(result["stock_rows"]) == 10
    assert len(result["non_stock_rows"]) == 3
    assert result["stock_rows"][0]["stock_code"] == "2330"
    assert result["stock_rows"][0]["stock_name"] == "台灣積體電路製造"
    page.wait_for_selector.assert_awaited_once_with(
        "table tr",
        state="attached",
        timeout=15000,
    )


@patch("scrapers.official.get_official_config", return_value=_config())
@pytest.mark.asyncio
async def test_jpmorgan_all_advertised_stock_rows_are_parsed(mock_config):
    page = _mock_page("基金資產 - 股票\n截至 2026/07/22\n共 61 筆")
    _set_tables(page, _generated_stock_rows(61))

    result = await scrape_jpmorgan_playwright("00401A", page)

    assert result["ok"] is True
    assert len(result["stock_rows"]) == 61
    assert result["stock_rows"][-1]["stock_code"] == "1060"


@patch("scrapers.official.get_official_config", return_value=_config())
@pytest.mark.asyncio
async def test_jpmorgan_partial_advertised_snapshot_fails_closed(mock_config):
    page = _mock_page("基金資產 - 股票\n截至 2026/07/22\n共 61 筆")
    _set_tables(page, STOCK_TABLE_ROWS)

    result = await scrape_jpmorgan_playwright("00401A", page)

    assert result["ok"] is False
    assert result["reason"] == "incomplete_jpmorgan_stock_rows:10/61"
    assert result["all_rows"] == []


@patch("scrapers.official.get_official_config", return_value=_config())
@pytest.mark.asyncio
async def test_jpmorgan_navigation_timeout_returns_failed_result(mock_config):
    page = _mock_page()
    page.goto.side_effect = PlaywrightTimeoutError("navigation timeout")

    result = await scrape_jpmorgan_playwright("00401A", page)

    assert result["ok"] is False
    assert "navigation failed" in result["reason"]


@patch("scrapers.official.get_official_config", return_value=_config())
@pytest.mark.asyncio
async def test_jpmorgan_post_navigation_parse_error_returns_failed_result(mock_config):
    page = _mock_page()
    page.query_selector_all.side_effect = RuntimeError("detached table")

    result = await scrape_jpmorgan_playwright("00401A", page)

    assert result["ok"] is False
    assert result["reason"] == "JPMorgan scrape failed: detached table"


@pytest.mark.asyncio
async def test_jpmorgan_dispatcher_routes_to_issuer_handler():
    page = AsyncMock()
    expected = {"ok": True, "source_type": "official_fallback"}

    with (
        patch("scrapers.official.get_official_config", return_value=_config()),
        patch(
            "scrapers.official.scrape_jpmorgan_playwright",
            new=AsyncMock(return_value=expected),
        ) as handler,
    ):
        result = await scrape_official_with_browser("00401a", page)

    assert result == expected
    handler.assert_awaited_once_with("00401A", page)


@pytest.mark.asyncio
async def test_jpmorgan_date_prefers_portfolio_section_date():
    page = _mock_page(
        "績效截至 2026/07/21\n"
        "基金資產 - 股票\n"
        "截至 2026/07/22\n"
        "共 10 筆"
    )

    assert await _extract_jpmorgan_holdings_date(page) == "2026/07/22"


@pytest.mark.asyncio
async def test_parse_stock_table_single_row():
    table = _mock_table(
        [
            ["股票代碼", "股票名稱", "股數", "金額", "權重(%)"],
            ["2330", "台灣積體電路製造", "110,000", "264,000,000", "8.66%"],
        ]
    )

    rows = await _parse_jpmorgan_stock_rows(
        table, "00401A", JPMORGAN_URL, "2026/07/22"
    )

    assert rows[0]["stock_code"] == "2330"
    assert rows[0]["stock_name"] == "台灣積體電路製造"
    assert rows[0]["shares"] == 110000
    assert rows[0]["market_value"] == 264000000
    assert rows[0]["weight_pct"] == 8.66


@pytest.mark.asyncio
async def test_parse_derivative_table_preserves_raw_name_and_code():
    rows = await _parse_jpmorgan_derivative_rows(
        _mock_table(FUTURE_ROWS), "00401A", JPMORGAN_URL, "2026/07/22"
    )

    assert rows[0]["asset_type"] == "futures"
    assert rows[0]["stock_code"] == "FTQ6"
    assert rows[0]["stock_name"] == "臺股期貨08/26"
    assert rows[0]["weight_pct"] == -0.18


@pytest.mark.asyncio
async def test_parse_cash_table_preserves_raw_name():
    rows = await _parse_jpmorgan_cash_rows(
        _mock_table(CASH_ROWS), "00401A", JPMORGAN_URL, "2026/07/22"
    )

    assert rows[0]["asset_type"] == "cash"
    assert rows[0]["stock_name"] == "NEW TAIWAN DOLLAR"
    assert rows[0]["market_value"] == 283733974
    assert rows[0]["weight_pct"] == 9.30


@pytest.mark.asyncio
async def test_invalid_stock_and_derivative_codes_are_skipped():
    invalid_stock = _mock_table(
        [
            ["股票代碼", "股票名稱", "股數", "金額", "權重(%)"],
            ["N/A", "Invalid Entry", "0", "0", "0.00%"],
        ]
    )
    invalid_derivative = _mock_table(
        [
            ["商品代碼", "商品名稱", "商品數量 (口數)", "權重 (%)"],
            ["N/A", "Bad Derivative", "0", "0.00%"],
        ]
    )

    assert await _parse_jpmorgan_stock_rows(
        invalid_stock, "00401A", JPMORGAN_URL, "2026/07/22"
    ) == []
    assert await _parse_jpmorgan_derivative_rows(
        invalid_derivative, "00401A", JPMORGAN_URL, "2026/07/22"
    ) == []
