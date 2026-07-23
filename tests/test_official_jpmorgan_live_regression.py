import math
from unittest.mock import AsyncMock, Mock, patch

import pytest

from scrapers.official import scrape_jpmorgan_playwright


class _Cell:
    def __init__(self, text):
        self._text = text

    async def inner_text(self):
        return self._text


class _Row:
    def __init__(self, values):
        self._values = values

    async def inner_text(self):
        return "\t".join(self._values)

    async def query_selector_all(self, selector):
        assert selector == "td"
        return [_Cell(value) for value in self._values]


class _PaginatedStockTable:
    def __init__(self, *, advance_pages=True):
        self._header = ["股票代碼", "股票名稱", "股數", "金額", "權重(%)"]
        self._rows = [
            [
                str(1000 + offset),
                f"測試股票{offset + 1}",
                "1,000",
                "1,000,000",
                "1.50%",
            ]
            for offset in range(61)
        ]
        self.page_size = 10
        self.current_page = 1
        self.advance_pages = advance_pages
        self.view_50_clicked = False
        self.next_clicks = 0

    @property
    def total_pages(self):
        return math.ceil(len(self._rows) / self.page_size)

    async def is_visible(self):
        return True

    async def query_selector(self, selector):
        assert selector == "tr:first-child"
        return _Row(self._header)

    async def query_selector_all(self, selector):
        assert selector == "tr"
        start = (self.current_page - 1) * self.page_size
        stop = start + self.page_size
        page_rows = self._rows[start:stop]
        return [_Row(self._header), *[_Row(values) for values in page_rows]]

    async def evaluate(self, script, arg=None):
        if "view-50" in script:
            self.page_size = 50
            self.current_page = 1
            self.view_50_clicked = True
            return True
        if "pagination-input" in script and "total-pages" in script:
            return {
                "current_page": self.current_page,
                "total_pages": self.total_pages,
            }
        if "right-chevron" in script:
            self.next_clicks += 1
            if self.advance_pages and self.current_page < self.total_pages:
                self.current_page += 1
                return True
            return False
        raise AssertionError(f"unexpected evaluate script: {script}")


class _Page:
    def __init__(self, stock_table):
        self.stock_table = stock_table
        self.goto = AsyncMock()
        self.wait_for_selector = AsyncMock()
        self.wait_for_timeout = AsyncMock()
        body = AsyncMock()
        body.inner_text = AsyncMock(
            return_value="基金資產 - 股票\n截至 2026/07/22"
        )
        self.locator = Mock(return_value=body)

    async def query_selector_all(self, selector):
        assert selector == "table"
        return [self.stock_table]


_CONFIG = {
    "url": "https://example.test/00401A#/portfolio",
    "method": "playwright",
    "issuer": "JPMorgan",
}


@patch("scrapers.official.get_official_config", return_value=_CONFIG)
@pytest.mark.asyncio
async def test_jpmorgan_waits_for_attached_rows_not_first_visible_row(mock_config):
    page = _Page(_PaginatedStockTable())

    await scrape_jpmorgan_playwright("00401A", page)

    page.wait_for_selector.assert_awaited_once_with(
        "table tr",
        state="attached",
        timeout=15000,
    )


@patch("scrapers.official.get_official_config", return_value=_CONFIG)
@pytest.mark.asyncio
async def test_jpmorgan_expands_and_collects_every_stock_page(mock_config):
    stock_table = _PaginatedStockTable()
    page = _Page(stock_table)

    result = await scrape_jpmorgan_playwright("00401A", page)

    assert result["ok"] is True
    assert len(result["stock_rows"]) == 61
    assert result["stock_rows"][0]["stock_code"] == "1000"
    assert result["stock_rows"][-1]["stock_code"] == "1060"
    assert stock_table.view_50_clicked is True
    assert stock_table.next_clicks == 1


@patch("scrapers.official.get_official_config", return_value=_CONFIG)
@pytest.mark.asyncio
async def test_jpmorgan_pagination_stall_fails_closed(mock_config):
    stock_table = _PaginatedStockTable(advance_pages=False)
    page = _Page(stock_table)

    result = await scrape_jpmorgan_playwright("00401A", page)

    assert result["ok"] is False
    assert result["reason"] == "JPMorgan scrape failed: stock pagination did not advance"
    assert result["all_rows"] == []
