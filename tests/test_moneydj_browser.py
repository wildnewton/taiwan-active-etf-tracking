import asyncio
from unittest.mock import AsyncMock

import pytest

from scrapers.moneydj import build_moneydj_url
from scrapers.moneydj_browser import (
    extract_all_dom_rows,
    extract_rows_by_pagination,
    scrape_moneydj_browser,
)


ETF_CODE = "00980A"
DATE = "2026/06/18"
SOURCE_URL = build_moneydj_url(ETF_CODE)
SAMPLE_ROWS = [
    ["台積電(2330.TW)", "9.36", "704,000"],
    ["台達電(2308.TW)", "5.64", "475,000"],
    ["聯發科(2454.TW)", "4.55", "188,000"],
]


def run(coro):
    return asyncio.run(coro)


def make_page():
    page = AsyncMock()
    page.locator.return_value.inner_text = AsyncMock(return_value="1/1")
    page.wait_for_load_state = AsyncMock()
    return page


def test_extract_all_dom_rows():
    page = make_page()
    page.eval_on_selector_all = AsyncMock(return_value=SAMPLE_ROWS)

    rows = run(extract_all_dom_rows(page, ETF_CODE, DATE, SOURCE_URL))

    assert len(rows) == 3
    assert rows[0]["date"] == DATE
    assert rows[0]["etf_code"] == ETF_CODE
    assert rows[0]["asset_name"] == "台積電(2330.TW)"
    assert rows[0]["asset_type"] == "stock"
    assert rows[0]["stock_code"] == "2330"
    assert rows[0]["stock_name"] == "台積電"
    assert rows[0]["weight_pct"] == 9.36
    assert rows[0]["shares"] == 704000
    assert rows[0]["source_url"] == SOURCE_URL
    assert rows[0]["source_type"] == "moneydj_browser"
    assert rows[0]["extraction_method"] == "playwright_dom"
    page.eval_on_selector_all.assert_awaited_once()


def test_extract_all_dom_rows_handles_empty():
    page = make_page()
    page.eval_on_selector_all = AsyncMock(return_value=[])

    rows = run(extract_all_dom_rows(page, ETF_CODE, DATE, SOURCE_URL))

    assert rows == []


def test_extract_rows_by_pagination():
    page = make_page()
    page.locator.return_value.inner_text = AsyncMock(return_value="1/2")
    page.eval_on_selector_all = AsyncMock(
        side_effect=[
            [SAMPLE_ROWS[0], SAMPLE_ROWS[1]],
            [SAMPLE_ROWS[1], SAMPLE_ROWS[2]],
        ]
    )
    page.select_option = AsyncMock()

    rows = run(extract_rows_by_pagination(page, ETF_CODE, DATE, SOURCE_URL))

    assert [row["asset_name"] for row in rows] == [
        "台積電(2330.TW)",
        "台達電(2308.TW)",
        "聯發科(2454.TW)",
    ]
    assert page.eval_on_selector_all.await_count == 2
    page.select_option.assert_awaited_once_with("select#pageselect", value="2")


def test_scrape_moneydj_browser_integration():
    page = make_page()
    page.inner_text = AsyncMock(return_value=f"資料日期 {DATE}")
    page.eval_on_selector_all = AsyncMock(return_value=SAMPLE_ROWS)
    page.goto = AsyncMock()

    result = run(scrape_moneydj_browser(ETF_CODE, page))

    assert result["ok"] is False
    assert result["reason"] == "fewer_than_5_rows"
    assert len(result["all_rows"]) == 3
    assert len(result["stock_rows"]) == 3
    assert result["non_stock_rows"] == []
    assert result["source_url"] == SOURCE_URL
    assert result["source_type"] == "moneydj_browser"
    assert result["total_weight_all_rows"] == 19.55
    assert result["total_weight_stock_rows"] == 19.55
    page.goto.assert_awaited_once_with(SOURCE_URL, wait_until="domcontentloaded")


def test_scrape_moneydj_browser_fallback_to_pagination():
    page = make_page()
    page.inner_text = AsyncMock(return_value=f"資料日期 {DATE}")
    page.locator.return_value.inner_text = AsyncMock(return_value="1/2")
    page.eval_on_selector_all = AsyncMock(
        side_effect=[
            [SAMPLE_ROWS[0], SAMPLE_ROWS[1]],
            [SAMPLE_ROWS[0], SAMPLE_ROWS[1]],
            [SAMPLE_ROWS[1], SAMPLE_ROWS[2]],
        ]
    )
    page.goto = AsyncMock()
    page.select_option = AsyncMock()

    result = run(scrape_moneydj_browser(ETF_CODE, page))

    assert [row["asset_name"] for row in result["all_rows"]] == [
        "台積電(2330.TW)",
        "台達電(2308.TW)",
        "聯發科(2454.TW)",
    ]
    assert result["source_type"] == "moneydj_browser"
    assert page.eval_on_selector_all.await_count == 3
    page.select_option.assert_awaited_once_with("select#pageselect", value="2")


@pytest.mark.parametrize(
    ("weight_text", "expected_total", "expected_reason"),
    [
        ("10.00", 50.0, "total_weight_below_expected_range"),
        ("30.00", 150.0, "total_weight_above_expected_range"),
    ],
)
def test_scrape_moneydj_browser_surfaces_weight_warning(
    weight_text,
    expected_total,
    expected_reason,
):
    raw_rows = [
        [f"測試股票{i}({1000 + i}.TW)", weight_text, "1,000"]
        for i in range(5)
    ]
    page = make_page()
    page.inner_text = AsyncMock(return_value=f"資料日期 {DATE}")
    page.eval_on_selector_all = AsyncMock(return_value=raw_rows)
    page.goto = AsyncMock()

    result = run(scrape_moneydj_browser(ETF_CODE, page))

    assert result["ok"] is True
    assert result["reason"] == "ok"
    assert result["weight_warning"] == {
        "reason": expected_reason,
        "source_total_weight_all_rows": expected_total,
        "minimum_expected_weight": 70.0,
        "maximum_expected_weight": 140.0,
    }
