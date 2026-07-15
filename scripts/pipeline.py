import asyncio
from datetime import date, datetime, time, timedelta
from typing import Awaitable, Callable, Optional
from zoneinfo import ZoneInfo

from db import (
    init_db,
    insert_scrape_run,
    replace_daily_snapshot,
    snapshot_exists,
    successful_snapshot_exists,
)
from etf_universe import get_active_etfs, get_etf_config, seed_etf_universe_from_file
from models import HoldingRow, NonStockAssetRow, ScrapeRun
from scraper import (
    FAILED_RESULT,
    scrape_holdings,
    scrape_holdings_with_browser_async,
)
from trading_calendar import is_tw_trading_day, latest_tw_trading_day_on_or_before


ScrapeFn = Callable[[str, date], dict]
AsyncScrapeFn = Callable[[str, date], Awaitable[dict]]

TAIPEI_TIMEZONE = ZoneInfo("Asia/Taipei")
DATA_AVAILABILITY_CUTOFF = time(15, 0)
_ASYNC_SCRAPE_CONCURRENCY = 3


def run_daily_scrape(db_path: str = "data/active_etf_holdings.sqlite") -> dict:
    return _run_daily_scrape_sync(db_path, scrape_holdings)


def run_daily_scrape_with_browser(
    db_path: str = "data/active_etf_holdings.sqlite",
) -> dict:
    return asyncio.run(run_daily_scrape_with_browser_async(db_path))


def run_selected_scrape_with_browser(
    db_path: str,
    etf_codes: list[str],
    run_date=None,
) -> dict:
    return asyncio.run(run_selected_scrape_with_browser_async(db_path, etf_codes, run_date=_coerce_run_date(run_date)))


async def run_daily_scrape_with_browser_async(
    db_path: str = "data/active_etf_holdings.sqlite",
    page=None,
) -> dict:
    if page is not None:
        return await _run_scrape_async(
            db_path,
            None,
            _browser_scrape_fn(page),
        )

    run_date, expected_data_date, summary, etfs_to_scrape = _prepare_scrape_run(
        db_path,
        None,
    )
    if not etfs_to_scrape:
        _finalize_data_date_range(summary)
        return summary

    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            context = await browser.new_context(locale="zh-TW")
            try:
                return await _execute_scrape_async_with_pages(
                    etfs_to_scrape,
                    context,
                    run_date,
                    expected_data_date,
                    summary,
                )
            finally:
                await context.close()
        finally:
            await browser.close()


async def run_selected_scrape_with_browser_async(
    db_path: str,
    etf_codes: list[str],
    page=None,
    run_date=None,
) -> dict:
    selected_etfs = [{"code": code} for code in etf_codes]
    run_date = _coerce_run_date(run_date)
    if page is not None:
        return await _run_scrape_async(
            db_path,
            selected_etfs,
            _browser_scrape_fn(page),
            run_date=run_date,
            use_trading_calendar=False,
            skip_existing_snapshot=False,
        )

    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            context = await browser.new_context(locale="zh-TW")
            try:
                browser_page = await context.new_page()
                return await _run_scrape_async(
                    db_path,
                    selected_etfs,
                    _browser_scrape_fn(browser_page),
                    run_date=run_date,
                    use_trading_calendar=False,
                    skip_existing_snapshot=False,
                )
            finally:
                await context.close()
        finally:
            await browser.close()


def _browser_scrape_fn(page) -> AsyncScrapeFn:
    async def scrape_one(etf_code: str, target_date: date) -> dict:
        return await scrape_holdings_with_browser_async(
            etf_code,
            page,
            target_date=target_date,
        )

    return scrape_one


def _active_etfs_for_run(run_date: date) -> list[dict]:
    seed_etf_universe_from_file()
    return get_active_etfs(as_of_date=run_date)


def _run_daily_scrape_sync(db_path: str, scrape_fn: ScrapeFn) -> dict:
    init_db(db_path)
    run_at = _as_taipei_run_at(_current_run_at())
    active_etfs = _active_etfs_for_run(run_at.date())
    return _run_scrape_sync(
        db_path,
        active_etfs,
        scrape_fn,
        already_initialized=True,
        run_at=run_at,
    )


def _run_scrape_sync(
    db_path: str,
    etfs: list[dict],
    scrape_fn: ScrapeFn,
    already_initialized: bool = False,
    use_trading_calendar: bool = True,
    run_at: datetime | None = None,
    skip_existing_snapshot: bool = True,
) -> dict:
    run_date, expected_data_date, summary, etfs_to_scrape = _prepare_scrape_run(
        db_path,
        etfs,
        already_initialized=already_initialized,
        use_trading_calendar=use_trading_calendar,
        run_at=run_at,
        skip_existing_snapshot=skip_existing_snapshot,
    )
    return _execute_scrape_sync(
        etfs_to_scrape,
        scrape_fn,
        run_date,
        expected_data_date,
        summary,
    )


async def _run_scrape_async(
    db_path: str,
    etfs: list[dict] | None,
    scrape_fn: AsyncScrapeFn,
    run_date=None,
    use_trading_calendar: bool = True,
    skip_existing_snapshot: bool = True,
) -> dict:
    run_date, expected_data_date, summary, etfs_to_scrape = _prepare_scrape_run(
        db_path,
        etfs,
        run_date=run_date,
        use_trading_calendar=use_trading_calendar,
        skip_existing_snapshot=skip_existing_snapshot,
    )
    return await _execute_scrape_async(
        etfs_to_scrape,
        scrape_fn,
        run_date,
        expected_data_date,
        summary,
    )


def _prepare_scrape_run(
    db_path: str,
    etfs: list[dict] | None,
    *,
    already_initialized: bool = False,
    use_trading_calendar: bool = True,
    run_at: datetime | None = None,
    run_date=None,
    skip_existing_snapshot: bool = True,
) -> tuple[date, Optional[date], dict, list[dict]]:
    if not already_initialized:
        init_db(db_path)

    if run_at is not None:
        run_at = _as_taipei_run_at(run_at)
    elif run_date is None:
        run_at = _as_taipei_run_at(_current_run_at())
    else:
        run_date = _coerce_run_date(run_date)
        run_at = datetime.combine(
            run_date,
            DATA_AVAILABILITY_CUTOFF,
            tzinfo=TAIPEI_TIMEZONE,
        )

    run_date = run_at.date()
    if etfs is None:
        etfs = _active_etfs_for_run(run_date)
    etfs = list(etfs)

    expected_data_date = _expected_data_date_for_run(run_at, use_trading_calendar)
    is_trading_day = _is_trading_day_for_run(run_date, use_trading_calendar)
    summary = _new_summary(run_date, len(etfs), expected_data_date, is_trading_day)

    if _should_skip_non_trading_day(is_trading_day, use_trading_calendar):
        _record_non_trading_day_skip(summary, len(etfs))
        return run_date, expected_data_date, summary, []

    if not skip_existing_snapshot:
        return run_date, expected_data_date, summary, etfs

    preexisting, etfs_to_scrape = _partition_preexisting_successes(
        etfs,
        expected_data_date,
    )
    _record_preexisting_success(summary, len(preexisting), expected_data_date)
    return run_date, expected_data_date, summary, etfs_to_scrape


def _partition_preexisting_successes(
    etfs: list[dict],
    expected_data_date: Optional[date],
) -> tuple[list[dict], list[dict]]:
    if expected_data_date is None:
        return [], list(etfs)

    preexisting = []
    missing = []
    for etf in etfs:
        target = (
            preexisting
            if successful_snapshot_exists(expected_data_date, etf["code"])
            else missing
        )
        target.append(etf)
    return preexisting, missing


def _record_preexisting_success(
    summary: dict,
    count: int,
    expected_data_date: Optional[date],
) -> None:
    if count <= 0 or expected_data_date is None:
        return
    summary["preexisting_success"] += count
    summary["data_freshness"]["fresh"] += count
    summary["_known_data_dates"].extend([expected_data_date] * count)


def _execute_scrape_sync(
    etfs: list[dict],
    scrape_fn: ScrapeFn,
    run_date: date,
    expected_data_date: Optional[date],
    summary: dict,
) -> dict:
    freshness_target_date = expected_data_date or run_date
    for etf in etfs:
        etf_code = etf["code"]
        started_at = datetime.now()
        result = scrape_fn(etf_code, freshness_target_date)
        finished_at = datetime.now()
        _record_result(
            summary,
            etf_code,
            run_date,
            expected_data_date,
            started_at,
            finished_at,
            result,
        )

    _finalize_data_date_range(summary)
    return summary


async def _execute_scrape_async(
    etfs: list[dict],
    scrape_fn: AsyncScrapeFn,
    run_date: date,
    expected_data_date: Optional[date],
    summary: dict,
) -> dict:
    freshness_target_date = expected_data_date or run_date
    for etf in etfs:
        etf_code = etf["code"]
        started_at = datetime.now()
        result = await scrape_fn(etf_code, freshness_target_date)
        finished_at = datetime.now()
        _record_result(
            summary,
            etf_code,
            run_date,
            expected_data_date,
            started_at,
            finished_at,
            result,
        )

    _finalize_data_date_range(summary)
    return summary


async def _execute_scrape_async_with_pages(
    etfs: list[dict],
    context,
    run_date: date,
    expected_data_date: Optional[date],
    summary: dict,
) -> dict:
    """Scrape concurrently, then record sequentially in ETF input order."""
    freshness_target_date = expected_data_date or run_date
    semaphore = asyncio.Semaphore(_ASYNC_SCRAPE_CONCURRENCY)

    async def scrape_one(etf: dict):
        etf_code = etf["code"]
        async with semaphore:
            started_at = datetime.now()
            page = None
            try:
                page = await context.new_page()
                result = await scrape_holdings_with_browser_async(
                    etf_code,
                    page,
                    target_date=freshness_target_date,
                )
            except Exception as exc:
                result = {
                    **FAILED_RESULT,
                    "reason": f"unhandled scraper exception: {exc}",
                }
            finally:
                if page is not None:
                    try:
                        await page.close()
                    except Exception as exc:
                        close_reason = f"unhandled page close exception: {exc}"
                        if result.get("ok") is False and result.get("reason"):
                            result = {
                                **result,
                                "reason": f"{result['reason']}; {close_reason}",
                            }
                        else:
                            result = {
                                **FAILED_RESULT,
                                "reason": close_reason,
                            }
            finished_at = datetime.now()
            return etf_code, started_at, finished_at, result

    outcomes = await asyncio.gather(*(scrape_one(etf) for etf in etfs))
    for etf_code, started_at, finished_at, result in outcomes:
        _record_result(
            summary,
            etf_code,
            run_date,
            expected_data_date,
            started_at,
            finished_at,
            result,
        )

    _finalize_data_date_range(summary)
    return summary


def _current_run_at() -> datetime:
    return datetime.now(TAIPEI_TIMEZONE)


def _as_taipei_run_at(run_at: datetime) -> datetime:
    if run_at.tzinfo is None:
        return run_at.replace(tzinfo=TAIPEI_TIMEZONE)
    return run_at.astimezone(TAIPEI_TIMEZONE)


def _expected_data_date_for_run(
    run_at: datetime,
    use_trading_calendar: bool,
) -> Optional[date]:
    local_run_at = _as_taipei_run_at(run_at)
    run_date = local_run_at.date()
    if not use_trading_calendar:
        return run_date
    candidate_date = run_date
    if local_run_at.time() < DATA_AVAILABILITY_CUTOFF:
        candidate_date -= timedelta(days=1)
    return latest_tw_trading_day_on_or_before(candidate_date)


def _is_trading_day_for_run(
    run_date: date,
    use_trading_calendar: bool,
) -> Optional[bool]:
    if not use_trading_calendar:
        return True
    return is_tw_trading_day(run_date)


def _should_skip_non_trading_day(
    is_trading_day: Optional[bool],
    use_trading_calendar: bool,
) -> bool:
    return use_trading_calendar and is_trading_day is False


def _coerce_run_date(value):
    if value is None:
        return None
    if isinstance(value, str):
        return datetime.strptime(value, "%Y-%m-%d").date()
    if all(hasattr(value, attr) for attr in ("year", "month", "day", "isoformat")):
        return value
    raise TypeError("run_date must be a date, ISO date string, or None")


def _new_summary(
    run_date: date,
    total_etfs: int,
    expected_data_date: Optional[date] = None,
    is_trading_day: Optional[bool] = None,
) -> dict:
    return {
        "date": run_date.isoformat(),
        "expected_data_date": expected_data_date.isoformat() if expected_data_date else None,
        "is_trading_day": is_trading_day,
        "skip_reason": None,
        "total_etfs": total_etfs,
        "moneydj_success": 0,
        "official_success": 0,
        "failed": 0,
        "skipped_non_trading_day": 0,
        "preexisting_success": 0,
        "skipped_stale_existing": 0,
        "total_stock_rows": 0,
        "total_non_stock_rows": 0,
        "failures": [],
        "moneydj_warnings": [],
        "row_count_warnings": [],
        "weight_warnings": [],
        "data_freshness": {"fresh": 0, "stale": 0, "unknown": 0},
        "stale_etfs": [],
        "stale_existing_etfs": [],
        "unknown_date_etfs": [],
        "data_date_min": None,
        "data_date_max": None,
        "_known_data_dates": [],
    }


def _record_non_trading_day_skip(summary: dict, skipped_count: int) -> None:
    summary["skipped_non_trading_day"] = skipped_count
    summary["skip_reason"] = "tw_stock_market_closed"


def _validate_snapshot_dates(result: dict) -> tuple[Optional[date], Optional[str]]:
    rows = [
        *(result.get("stock_rows") or []),
        *(result.get("non_stock_rows") or []),
    ]
    if not rows:
        return None, "empty_snapshot"

    parsed_dates = []
    for row in rows:
        parsed = _parse_row_date(row.get("date"))
        if parsed is None:
            return None, "missing_or_unparseable_source_date"
        parsed_dates.append(parsed)

    unique_dates = set(parsed_dates)
    if len(unique_dates) != 1:
        return None, "inconsistent_source_dates"
    return parsed_dates[0], None



def _classify_scrape_status(
    result: dict,
    data_date: Optional[date],
    expected_data_date: date,
) -> str:
    """Return the final persisted status for one scrape result."""
    if result["ok"] is not True or data_date is None:
        return "failed"
    if data_date < expected_data_date:
        return "stale"
    if data_date == expected_data_date:
        return "success"
    return "failed"


def _record_result(
    summary: dict,
    etf_code: str,
    run_date: date,
    expected_data_date: Optional[date],
    started_at: datetime,
    finished_at: datetime,
    result: dict,
) -> None:
    should_record_scrape_run = True
    freshness_target_date = expected_data_date or run_date
    data_date = None
    final_result = result
    final_status = "failed"

    if result["ok"] is True:
        data_date, date_error = _validate_snapshot_dates(result)
        if date_error is not None:
            final_result = {**result, "ok": False, "reason": date_error}
            _record_freshness(
                summary,
                etf_code,
                freshness_target_date,
                None,
                result,
                unknown_reason=date_error,
            )
            _record_failure(
                summary,
                etf_code,
                date_error,
                run_moneydj_diagnostic=result.get("source_type")
                not in {"moneydj_primary", "moneydj_browser"},
            )
            insert_scrape_run(
                _build_scrape_run(
                    etf_code,
                    run_date,
                    None,
                    started_at,
                    finished_at,
                    final_result,
                    status="failed",
                )
            )
            return

        final_status = _classify_scrape_status(
            result,
            data_date,
            freshness_target_date,
        )
        _record_weight_warning(summary, etf_code, result)

        if final_status == "failed":
            reason = "source_date_after_run_date"
            final_result = {**result, "ok": False, "reason": reason}
            _record_freshness(
                summary,
                etf_code,
                freshness_target_date,
                data_date,
                result,
            )
            _record_failure(
                summary,
                etf_code,
                reason,
                run_moneydj_diagnostic=result.get("source_type")
                not in {"moneydj_primary", "moneydj_browser"},
            )
            insert_scrape_run(
                _build_scrape_run(
                    etf_code,
                    run_date,
                    data_date,
                    started_at,
                    finished_at,
                    final_result,
                    status=final_status,
                )
            )
            return

        if final_status == "stale" and _should_skip_stale_existing_snapshot(
            data_date,
            freshness_target_date,
            etf_code,
        ):
            _record_freshness(
                summary,
                etf_code,
                freshness_target_date,
                data_date,
                result,
            )
            _record_stale_existing(summary, etf_code, data_date, result)
            insert_scrape_run(
                _build_scrape_run(
                    etf_code,
                    run_date,
                    data_date,
                    started_at,
                    finished_at,
                    result,
                    status="skipped_stale_existing",
                )
            )
            return

        stock_rows = [_to_holding_row(row) for row in result["stock_rows"]]
        non_stock_rows = [
            _to_non_stock_asset_row(row) for row in result["non_stock_rows"]
        ]
        write_result = replace_daily_snapshot(stock_rows, non_stock_rows)
        should_record_scrape_run = write_result.get("inserted", False)

        summary["total_stock_rows"] += len(stock_rows)
        summary["total_non_stock_rows"] += len(non_stock_rows)
        if result["source_type"] in {"moneydj_primary", "moneydj_browser"}:
            summary["moneydj_success"] += 1
        elif result["source_type"] == "official_fallback":
            summary["official_success"] += 1
            _check_moneydj_warning(summary, etf_code)
        _record_freshness(
            summary,
            etf_code,
            freshness_target_date,
            data_date,
            result,
        )
        _record_row_count_warning(summary, etf_code, result)
    else:
        _record_failure(summary, etf_code, result["reason"])

    if should_record_scrape_run:
        insert_scrape_run(
            _build_scrape_run(
                etf_code,
                run_date,
                data_date,
                started_at,
                finished_at,
                final_result,
                status=final_status,
            )
        )

def _record_failure(
    summary: dict,
    etf_code: str,
    reason: str,
    run_moneydj_diagnostic: bool = True,
) -> None:
    summary["failed"] += 1
    summary["failures"].append({"etf_code": etf_code, "reason": reason})
    if run_moneydj_diagnostic:
        _check_moneydj_warning(summary, etf_code)


def _should_skip_stale_existing_snapshot(data_date: Optional[date], expected_data_date: date, etf_code: str) -> bool:
    if data_date is None or data_date >= expected_data_date:
        return False
    return snapshot_exists(data_date, etf_code)


def _record_stale_existing(summary: dict, etf_code: str, data_date: date, result: dict) -> None:
    summary["skipped_stale_existing"] += 1
    summary["stale_existing_etfs"].append({
        "etf_code": etf_code,
        "data_date": data_date.isoformat(),
        "source_type": result.get("source_type") or "unknown",
        "reason": "stale_snapshot_already_exists",
    })


def _record_weight_warning(summary: dict, etf_code: str, result: dict) -> None:
    warning = result.get("weight_warning")
    if not warning:
        return
    summary["weight_warnings"].append({"etf_code": etf_code, **warning})


def _record_row_count_warning(summary: dict, etf_code: str, result: dict) -> None:
    warning = result.get("row_count_warning")
    if not warning:
        return
    summary["row_count_warnings"].append({"etf_code": etf_code, **warning})


def _record_freshness(
    summary: dict,
    etf_code: str,
    target_date: date,
    data_date: Optional[date],
    result: dict,
    unknown_reason: str = "missing_or_unparseable_source_date",
) -> None:
    source_type = result.get("source_type") or "unknown"
    if data_date is None:
        summary["data_freshness"]["unknown"] += 1
        summary["unknown_date_etfs"].append({
            "etf_code": etf_code,
            "source_type": source_type,
            "reason": unknown_reason,
        })
        return

    if data_date > target_date:
        summary["data_freshness"]["unknown"] += 1
        summary["unknown_date_etfs"].append({
            "etf_code": etf_code,
            "source_type": source_type,
            "reason": "source_date_after_run_date",
        })
        return

    summary["_known_data_dates"].append(data_date)
    if data_date == target_date:
        summary["data_freshness"]["fresh"] += 1
    else:
        summary["data_freshness"]["stale"] += 1
        summary["stale_etfs"].append({
            "etf_code": etf_code,
            "data_date": data_date.isoformat(),
            "source_type": source_type,
            "reason": "source_date_before_run_date",
        })

def _finalize_data_date_range(summary: dict) -> None:
    known_dates = summary.pop("_known_data_dates", [])
    if not known_dates:
        return
    summary["data_date_min"] = min(known_dates).isoformat()
    summary["data_date_max"] = max(known_dates).isoformat()


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


def _require_row_date(row: dict) -> date:
    parsed = _parse_row_date(row.get("date"))
    if parsed is None:
        raise ValueError(f"Validated snapshot row has no parseable date for {row.get('etf_code')}")
    return parsed


def _to_holding_row(row: dict) -> HoldingRow:
    return HoldingRow(
        date=_require_row_date(row),
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


def _to_non_stock_asset_row(row: dict) -> NonStockAssetRow:
    return NonStockAssetRow(
        date=_require_row_date(row),
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
    data_date: Optional[date],
    started_at: datetime,
    finished_at: datetime,
    result: dict,
    status: str | None = None,
) -> ScrapeRun:
    source_type = result.get("source_type", "")
    final_status = status or ("success" if result["ok"] else "failed")
    usable_result = result["ok"] is True and final_status in {"success", "stale"}

    error = None
    if final_status == "skipped_stale_existing":
        error = "stale_snapshot_already_exists"
    elif final_status == "failed":
        error = result.get("reason")

    return ScrapeRun(
        date=scrape_date,
        data_date=data_date,
        etf_code=etf_code,
        status=final_status,
        primary_source=source_type or "none",
        primary_success=usable_result and source_type == "moneydj_primary",
        moneydj_browser_used=usable_result and source_type == "moneydj_browser",
        official_fallback_used=usable_result and source_type == "official_fallback",
        official_success=usable_result and source_type == "official_fallback",
        rows_extracted=len(result.get("all_rows", [])),
        stock_rows_extracted=len(result.get("stock_rows", [])),
        non_stock_rows_extracted=len(result.get("non_stock_rows", [])),
        total_weight_all_rows=result.get("total_weight_all_rows", 0.0),
        total_weight_stock_rows=result.get("total_weight_stock_rows", 0.0),
        source_url=result.get("source_url") or None,
        error=error,
        started_at=started_at,
        finished_at=finished_at,
    )

def _parse_row_date(value) -> Optional[date]:
    if isinstance(value, date):
        return value
    if not value:
        return None

    for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None
