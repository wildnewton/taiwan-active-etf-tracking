import inspect
import re

from scrapers.moneydj import (
    build_moneydj_url,
    classify_asset,
    dedupe_rows,
    parse_date,
    split_rows,
    validate_rows,
)


SOURCE_TYPE = "moneydj_browser"
DOM_EXTRACTION_METHOD = "playwright_dom"
PAGINATION_EXTRACTION_METHOD = "playwright_pagination"
VISIBLE_PAGE_ROW_LIMIT = 20


async def scrape_moneydj_browser(etf_code: str, page) -> dict:
    source_url = build_moneydj_url(etf_code)

    try:
        await page.goto(source_url, wait_until="domcontentloaded")
        body_text = await page.inner_text("body")
        data_date = parse_date(body_text)

        all_rows = await extract_all_dom_rows(page, etf_code, data_date, source_url)
        page_count = await _get_page_count(page)
        if page_count > 1 and len(all_rows) <= VISIBLE_PAGE_ROW_LIMIT:
            all_rows = await extract_rows_by_pagination(
                page,
                etf_code,
                data_date,
                source_url,
            )

        all_rows = dedupe_rows(all_rows)
        ok, reason = validate_rows(all_rows)
        stock_rows, non_stock_rows = split_rows(all_rows)
    except Exception as exc:
        return {
            "ok": False,
            "reason": str(exc),
            "all_rows": [],
            "stock_rows": [],
            "non_stock_rows": [],
            "source_url": source_url,
            "source_type": SOURCE_TYPE,
            "total_weight_all_rows": 0.0,
            "total_weight_stock_rows": 0.0,
        }

    return {
        "ok": ok,
        "reason": reason,
        "all_rows": all_rows,
        "stock_rows": stock_rows,
        "non_stock_rows": non_stock_rows,
        "source_url": source_url,
        "source_type": SOURCE_TYPE,
        "total_weight_all_rows": _sum_weights(all_rows),
        "total_weight_stock_rows": _sum_weights(stock_rows),
    }


async def extract_all_dom_rows(page, etf_code, data_date, source_url) -> list[dict]:
    raw_rows = await _extract_raw_rows(page, "table.datalist tbody tr")
    return [
        _build_row(
            raw_row,
            etf_code,
            data_date,
            source_url,
            DOM_EXTRACTION_METHOD,
        )
        for raw_row in raw_rows
        if _has_expected_cells(raw_row)
    ]


async def extract_rows_by_pagination(page, etf_code, data_date, source_url) -> list[dict]:
    total_pages = await _get_page_count(page)
    rows = []

    for page_number in range(1, total_pages + 1):
        if page_number > 1:
            await page.select_option("select#pageselect", value=str(page_number))
            await page.wait_for_load_state("domcontentloaded")

        raw_rows = await _extract_raw_rows(page, "table.datalist tbody tr:visible")
        rows.extend(
            _build_row(
                raw_row,
                etf_code,
                data_date,
                source_url,
                PAGINATION_EXTRACTION_METHOD,
            )
            for raw_row in raw_rows
            if _has_expected_cells(raw_row)
        )

    return dedupe_rows(rows)


async def _extract_raw_rows(page, selector: str) -> list[list[str]]:
    return await page.eval_on_selector_all(
        selector,
        """
        rows => rows.map(row =>
            Array.from(row.querySelectorAll("td")).map(td =>
                (td.textContent || "").trim()
            )
        )
        """,
    )


async def _get_page_count(page) -> int:
    try:
        locator = await _maybe_await(page.locator(".info"))
        info_text = await locator.inner_text()
    except Exception:
        return 1

    match = re.search(r"(\d+)\s*/\s*(\d+)", info_text or "")
    return int(match.group(2)) if match else 1


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


def _build_row(raw_row, etf_code, data_date, source_url, extraction_method) -> dict:
    asset_name, weight_text, shares_text = raw_row[:3]
    classification = classify_asset(asset_name)

    return {
        "date": data_date,
        "etf_code": etf_code.upper(),
        "asset_name": asset_name,
        "asset_type": classification["asset_type"],
        "stock_code": classification["stock_code"],
        "stock_name": classification["stock_name"],
        "shares": _parse_number(shares_text),
        "weight_pct": _parse_float(weight_text),
        "source_url": source_url,
        "source_type": SOURCE_TYPE,
        "extraction_method": extraction_method,
    }


def _has_expected_cells(raw_row) -> bool:
    return len(raw_row) == 3


def _parse_float(value: str) -> float | None:
    cleaned = value.strip().replace(",", "").replace("%", "")
    if not cleaned or cleaned in {"-", "--"}:
        return None
    result = float(cleaned)
    if result == 0.0:
        result = 0.004
    return result


def _parse_number(value: str) -> int | float | None:
    cleaned = value.strip().replace(",", "")
    if not cleaned or cleaned in {"-", "--"}:
        return None

    number = float(cleaned)
    return int(number) if number.is_integer() else number


def _sum_weights(rows: list) -> float:
    return round(
        sum(row["weight_pct"] for row in rows if row.get("weight_pct") is not None),
        2,
    )
