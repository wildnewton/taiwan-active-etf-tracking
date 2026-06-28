import asyncio
from datetime import date, datetime
from typing import Awaitable, Callable

from db import init_db, insert_holdings, insert_non_stock_assets, insert_scrape_run
from etf_universe import get_active_etfs, get_etf_config, seed_etf_universe_from_file
from models import HoldingRow, NonStockAssetRow, ScrapeRun
from scraper import scrape_holdings, scrape_holdings_with_browser_async


ScrapeFn = Callable[[str], dict]
AsyncScrapeFn = Callable[[str], Awaitable[dict]]


def run_daily_scrape(db_path: str = "data/active_etf_holdings.sqlite") -> dict:
    return _run_daily_scrape_sync(db_path, scrape_holdings)


def run_daily_scrape_with_browser(
    db_path: str = "data/active_etf_holdings.sqlite",
) -> dict:
    return asyncio.run(run_daily_scrape_with_browser_async(db_path))


async def run_daily_scrape_with_browser_async(
    db_path: str = "data/active_etf_holdings.sqlite",
    page=None,
) -> dict:
    if page is not None:
        return await _run_daily_scrape_async(
            db_path,
            lambda etf_code: scrape_holdings_with_browser_async(etf_code, page),
        )

    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            context = await browser.new_context(locale="zh-TW")
            try:
                browser_page = await context.new_page()
                return await _run_daily_scrape_async(
                    db_path,
                    lambda etf_code: scrape_holdings_with_browser_async(
                        etf_code,
                        browser_page,
                    ),
                )
            finally:
                await context.close()
        finally:
            await browser.close()


def _active_etfs_for_run() -> list[dict]:
    seed_etf_universe_from_file()
    return get_active_etfs()


def _run_daily_scrape_sync(db_path: str, scrape_fn: ScrapeFn) -> dict:
    init_db(db_path)
    active_etfs = _active_etfs_for_run()
    today = date.today()
    summary = _new_summary(today, len(active_etfs))
    data_date = None

    for etf in active_etfs:
        etf_code = etf["code"]
        started_at = datetime.now()
        result = scrape_fn(etf_code)
        finished_at = datetime.now()
        if data_date is None and result["ok"] is True:
            data_date = _extract_data_date(result, today)
            summary["data_date"] = data_date.isoformat()
        _record_result(summary, etf_code, data_date or today, started_at, finished_at, result)

    summary["date"] = (data_date or today).isoformat()
    return summary


async def _run_daily_scrape_async(db_path: str, scrape_fn: AsyncScrapeFn) -> dict:
    init_db(db_path)
    active_etfs = _active_etfs_for_run()
    today = date.today()
    summary = _new_summary(today, len(active_etfs))
    data_date = None

    for etf in active_etfs:
        etf_code = etf["code"]
        started_at = datetime.now()
        result = await scrape_fn(etf_code)
        finished_at = datetime.now()
        if data_date is None and result["ok"] is True:
            data_date = _extract_data_date(result, today)
            summary["data_date"] = data_date.isoformat()
        _record_result(summary, etf_code, data_date or today, started_at, finished_at, result)

    summary["date"] = (data_date or today).isoformat()
    return summary


def _new_summary(today: date, total_etfs: int) -> dict:
    return {
        "date": today.isoformat(),
        "data_date": None,
        "total_etfs": total_etfs,
        "moneydj_success": 0,
        "official_success": 0,
        "failed": 0,
        "total_stock_rows": 0,
        "total_non_stock_rows": 0,
        "failures": [],
        "moneydj_warnings": [],
    }


def _extract_data_date(result: dict, fallback: date) -> date:
    rows = result.get("all_rows") or result.get("stock_rows") or []
    for row in rows:
        parsed = _parse_row_date(row.get("date"), fallback)
        if parsed != fallback:
            return parsed
    return fallback


def _record_result(
    summary: dict,
    etf_code: str,
    today: date,
    started_at: datetime,
    finished_at: datetime,
    result: dict,
) -> None:
    if result["ok"] is True:
        stock_rows = [_to_holding_row(row, today) for row in result["stock_rows"]]
        non_stock_rows = [
            _to_non_stock_asset_row(row, today) for row in result["non_stock_rows"]
        ]
        insert_holdings(stock_rows)
        insert_non_stock_assets(non_stock_rows)

        summary["total_stock_rows"] += len(stock_rows)
        summary["total_non_stock_rows"] += len(non_stock_rows)
        if result["source_type"] in {"moneydj_primary", "moneydj_browser"}:
            summary["moneydj_success"] += 1
        elif result["source_type"] == "official_fallback":
            summary["official_success"] += 1
            _check_moneydj_warning(summary, etf_code)
    else:
        summary["failed"] += 1
        summary["failures"].append({"etf_code": etf_code, "reason": result["reason"]})
        _check_moneydj_warning(summary, etf_code)

    insert_scrape_run(_build_scrape_run(etf_code, today, started_at, finished_at, result))


def _check_moneydj_warning(summary: dict, etf_code: str) -> None:
    from scrapers.moneydj import scrape_moneydj

    result = scrape_moneydj(etf_code)
    if result["ok"] is False:
        cfg = get_etf_config(etf_code)
        summary["moneydj_warnings"].append({
            "etf_code": etf_code,
            "issuer": cfg.get("issuer", "unknown"),
            "reason": result.get("reason", "unknown"),
            "rows": len(result.get("all_rows", [])),
            "weight": result.get("total_weight_all_rows", 0.0),
            "url": result.get("source_url", ""),
        })


def _to_holding_row(row: dict, default_date: date) -> HoldingRow:
    return HoldingRow(
        date=_parse_row_date(row.get("date"), default_date),
        etf_code=row["etf_code"],
        asset_name=row["asset_name"],
        asset_type=row["asset_type"],
        stock_code=row.get("stock_code"),
        stock_name=row.get("stock_name"),
        shares=row.get("shares"),
        weight_pct=row["weight_pct"],
        source_url=row["source_url"],
        source_type=row["source_type"],
        extraction_method=row["extraction_method"],
        scraped_at=datetime.now(),
    )


def _to_non_stock_asset_row(row: dict, default_date: date) -> NonStockAssetRow:
    return NonStockAssetRow(
        date=_parse_row_date(row.get("date"), default_date),
        etf_code=row["etf_code"],
        asset_name=row["asset_name"],
        asset_type=row["asset_type"],
        weight_pct=row["weight_pct"],
        source_url=row["source_url"],
        source_type=row["source_type"],
        extraction_method=row["extraction_method"],
        scraped_at=datetime.now(),
    )


def _build_scrape_run(
    etf_code: str,
    scrape_date: date,
    started_at: datetime,
    finished_at: datetime,
    result: dict,
) -> ScrapeRun:
    source_type = result.get("source_type", "")
    return ScrapeRun(
        date=scrape_date,
        etf_code=etf_code,
        status="success" if result["ok"] else "failed",
        primary_source=source_type or "none",
        primary_success=source_type == "moneydj_primary",
        moneydj_browser_used=source_type == "moneydj_browser",
        official_fallback_used=source_type == "official_fallback",
        official_success=result["ok"] is True and source_type == "official_fallback",
        rows_extracted=len(result.get("all_rows", [])),
        stock_rows_extracted=len(result.get("stock_rows", [])),
        non_stock_rows_extracted=len(result.get("non_stock_rows", [])),
        total_weight_all_rows=result.get("total_weight_all_rows", 0.0),
        total_weight_stock_rows=result.get("total_weight_stock_rows", 0.0),
        source_url=result.get("source_url") or None,
        error=None if result["ok"] else result.get("reason"),
        started_at=started_at,
        finished_at=finished_at,
    )


def _parse_row_date(value, default_date: date) -> date:
    if isinstance(value, date):
        return value
    if not value:
        return default_date

    for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return default_date
