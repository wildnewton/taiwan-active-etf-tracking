from unittest.mock import AsyncMock, Mock, patch

import pytest

from scrapers.official import scrape_uni_president_playwright


UNI_PRESIDENT_URL = "https://www.ezmoney.com.tw/ETF/Fund/Info?fundCode=49YTW"


def _rich_uni_president_rows():
    header = ["股票代號", "名稱", "持股數", "佔基金淨資產比例(%)"]
    weights = [
        "18.29", "8.50", "6.12", "5.80", "5.45", "5.10", "4.80", "4.50",
        "4.20", "3.90", "3.60", "3.30", "3.00", "2.70", "2.50", "2.30",
        "2.10", "1.90", "1.70", "1.50", "1.30", "1.10", "0.90", "0.70",
    ]
    data_rows = [["2330", "台積電", "13,300,000", weight] for weight in weights]
    return [header] + data_rows


def _make_mock_table(rows_data):
    table = AsyncMock()
    mock_rows = []
    for index, row_cells in enumerate(rows_data):
        row = AsyncMock()
        row.inner_text = AsyncMock(return_value=" ".join(row_cells))
        if index == 0:
            row.query_selector_all = AsyncMock(return_value=[])
        else:
            cells = []
            for cell_text in row_cells:
                cell = AsyncMock()
                cell.inner_text = AsyncMock(return_value=cell_text)
                cells.append(cell)
            row.query_selector_all = AsyncMock(return_value=cells)
        mock_rows.append(row)
    table.query_selector_all = AsyncMock(return_value=mock_rows)
    return table


def _make_mock_page(body_text):
    page = AsyncMock()
    page.goto = AsyncMock()
    page.wait_for_selector = AsyncMock()
    page.query_selector_all = AsyncMock(return_value=[_make_mock_table(_rich_uni_president_rows())])
    body_locator = AsyncMock()
    body_locator.inner_text.return_value = body_text
    page.locator = Mock(return_value=body_locator)
    return page


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_uni_president_uses_labeled_holdings_date_not_page_render_date(mock_config):
    mock_config.return_value = {
        "url": UNI_PRESIDENT_URL,
        "method": "playwright",
        "issuer": "Uni-President",
        "internal_id": "49YTW",
        "official_logic": "internal_fundcode=49YTW",
    }
    page = _make_mock_page(
        "頁面產製時間：2026/07/10\n"
        "主動統一台股增長\n"
        "投資組合資料日期：2026/07/09\n"
        "股票投資明細"
    )

    result = await scrape_uni_president_playwright("00981A", page)

    assert result["ok"] is True
    assert len(result["stock_rows"]) == 24
    assert result["stock_rows"][0]["date"] == "2026/07/09"


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_uni_president_does_not_use_unlabeled_global_render_date(mock_config):
    mock_config.return_value = {
        "url": UNI_PRESIDENT_URL,
        "method": "playwright",
        "issuer": "Uni-President",
        "internal_id": "49YTW",
        "official_logic": "internal_fundcode=49YTW",
    }
    page = _make_mock_page(
        "頁面產製時間：2026/07/10\n"
        "主動統一台股增長\n"
        "股票投資明細"
    )

    result = await scrape_uni_president_playwright("00981A", page)

    assert result["ok"] is True
    assert len(result["stock_rows"]) == 24
    assert result["stock_rows"][0]["date"] is None
