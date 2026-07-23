"""Unified scraper — decision tree for all data sources.

Priority:
  1. MoneyDJ static (fastest, no browser) — retries up to 10 attempts
  2. MoneyDJ browser (Playwright fallback)
  3. Official browser-based (Capital API, Nomura stealth, Mega/Uni-President Playwright)
  4. Official static (Fubon, Taishin)
  5. Fail
"""

import asyncio
import sqlite3
import time
from datetime import date, datetime
from inspect import isawaitable

import db
from config import get_etf_config
from scrapers.moneydj import scrape_moneydj
from scrapers.moneydj_browser import scrape_moneydj_browser
from scrapers.official import scrape_official_static, scrape_official_with_browser
from snapshot_validation import MIN_TAIWAN_STOCK_ROWS, validate_snapshot_rows


FAILED_RESULT = {
    "ok": False,
    "reason": "all sources failed",
    "all_rows": [],
    "stock_rows": [],
    "non_stock_rows": [],
    "source_url": "",
    "source_type": "",
    "total_weight_all_rows": 0.0,
    "total_weight_stock_rows": 0.0,
}

_MONEYDJ_RETRIES = 10
_MONEYDJ_RETRY_DELAYS = []  # Fibonacci * 2: 2, 2, 4, 6, 10, 16, 26, 42, 68
_ROW_COUNT_HISTORY_DAYS = 5
_LOW_ROW_COUNT_THRESHOLD = 0.6
_MONEYDJ_SOURCE_TYPES = {"moneydj_primary", "moneydj_browser"}
_MIN_WEIGHT_THRESHOLD = 0.01


def _build_retry_delays(max_attempts: int) -> list[float]:
    """Generate Fibonacci * 2 delays for max_attempts - 1 gaps.

    Sequence: fib(1)*2, fib(2)*2, fib(3)*2, ... = 2, 2, 4, 6, 10, 16, ...
    """
    delays = []
    a, b = 1, 1  # fib(1), fib(2)
    for _ in range(max_attempts - 1):
        delays.append(a * 2)
        a, b = b, a + b
    return delays


_MONEYDJ_RETRY_DELAYS = _build_retry_delays(_MONEYDJ_RETRIES)


def _retry_moneydj(etf_code: str) -> dict:
    """Call scrape_moneydj synchronously with Fibonacci backoff."""
    last_result = FAILED_RESULT.copy()
    for attempt in range(_MONEYDJ_RETRIES):
        last_result = scrape_moneydj(etf_code)
        if last_result["ok"] is True:
            return last_result
        if attempt < _MONEYDJ_RETRIES - 1:
            time.sleep(_MONEYDJ_RETRY_DELAYS[attempt])
    return last_result


async def _retry_moneydj_async(etf_code: str) -> dict:
    """Run MoneyDJ attempts off the event loop with async backoff."""
    last_result = FAILED_RESULT.copy()
    for attempt in range(_MONEYDJ_RETRIES):
        last_result = await asyncio.to_thread(scrape_moneydj, etf_code)
        if last_result["ok"] is True:
            return last_result
        if attempt < _MONEYDJ_RETRIES - 1:
            await asyncio.sleep(_MONEYDJ_RETRY_DELAYS[attempt])
    return last_result


def _require_target_date(target_date: date | None) -> date:
    if target_date is None:
        raise TypeError("target_date is required")
    return target_date


def scrape_holdings(etf_code: str, target_date: date) -> dict:
    """Scrape holdings without browser using the caller-provided freshness target."""
    target_date = _require_target_date(target_date)
    moneydj_result = _retry_moneydj(etf_code)
    if moneydj_result["ok"] is True:
        moneydj_result = _normalize_source_result(
            moneydj_result,
            "moneydj_primary",
        )
    if moneydj_result["ok"] is True:
        official_candidate = None
        if _is_stale_result(moneydj_result, target_date):
            official_candidate = _official_fallback_static(etf_code)
            if official_candidate["ok"] is True and _is_fresh_result(official_candidate, target_date):
                return official_candidate
        return _maybe_replace_low_row_count_sync(etf_code, moneydj_result, official_candidate)

    official_result = _official_fallback_static(etf_code)
    if official_result["ok"] is True:
        return official_result

    return FAILED_RESULT.copy()


def scrape_holdings_with_browser(
    etf_code: str,
    page,
    target_date: date,
) -> dict:
    """Sync wrapper for the full browser decision tree.

    Use this from synchronous code when no event loop is running. Async callers
    should call scrape_holdings_with_browser_async directly.
    """
    target_date = _require_target_date(target_date)
    return _run_async(
        scrape_holdings_with_browser_async(
            etf_code,
            page,
            target_date=target_date,
        )
    )


async def scrape_holdings_with_browser_async(
    etf_code: str,
    page,
    target_date: date,
) -> dict:
    """Async browser-enabled full decision tree.

    MoneyDJ static → MoneyDJ browser → Official browser → Official static → Fail.
    This is the production-safe path for an async Playwright pipeline because it
    avoids nesting asyncio.run inside an already-running event loop.
    """
    target_date = _require_target_date(target_date)
    # 1. MoneyDJ static (fastest) — synchronous request work runs off-loop.
    moneydj_result = await _retry_moneydj_async(etf_code)
    if moneydj_result["ok"] is True:
        moneydj_result = _normalize_source_result(
            moneydj_result,
            "moneydj_primary",
        )
    if moneydj_result["ok"] is True:
        official_candidate = None
        if _is_stale_result(moneydj_result, target_date):
            official_candidate = await _official_fallback_with_browser(etf_code, page, target_date=target_date)
            if official_candidate["ok"] is True and _is_fresh_result(official_candidate, target_date):
                return official_candidate
        return await _maybe_replace_low_row_count_async(
            etf_code,
            moneydj_result,
            page,
            official_candidate,
            target_date=target_date,
        )

    # 2. MoneyDJ browser
    browser_result = await scrape_moneydj_browser(etf_code, page)
    if browser_result["ok"] is True:
        browser_result = _normalize_source_result(
            browser_result,
            "moneydj_browser",
        )
    if browser_result["ok"] is True:
        official_candidate = None
        if _is_stale_result(browser_result, target_date):
            official_candidate = await _official_fallback_with_browser(etf_code, page, target_date=target_date)
            if official_candidate["ok"] is True and _is_fresh_result(official_candidate, target_date):
                return official_candidate
        return await _maybe_replace_low_row_count_async(
            etf_code,
            browser_result,
            page,
            official_candidate,
            target_date=target_date,
        )

    # 3-4. Official fallbacks after MoneyDJ failure.
    official_result = await _official_fallback_with_browser(etf_code, page, target_date=target_date)
    if official_result["ok"] is True:
        return official_result

    return FAILED_RESULT.copy()


async def _official_fallback_with_browser(
    etf_code: str,
    page,
    target_date: date | None = None,
) -> dict:
    config = get_etf_config(etf_code)
    if config["official_method"] in ("api", "stealth_api", "playwright", "browser"):
        if config.get("issuer") == "JPMorgan":
            official_browser = await scrape_official_with_browser(
                etf_code,
                page,
                target_date=target_date,
            )
        else:
            official_browser = await scrape_official_with_browser(etf_code, page)
        if official_browser["ok"] is True:
            official_browser = _normalize_source_result(
                official_browser,
                "official_fallback",
            )
            if official_browser["ok"] is True:
                return official_browser

    return await asyncio.to_thread(_official_fallback_static, etf_code)


def _official_fallback_static(etf_code: str) -> dict:
    official_result = scrape_official_static(etf_code)
    if official_result["ok"] is True:
        return _normalize_source_result(
            official_result,
            "official_fallback",
        )
    return official_result


def get_historical_mean_stock_row_count(etf_code: str, source_type: str, limit: int = _ROW_COUNT_HISTORY_DAYS):
    """Return recent historical mean stock row count for one ETF/source.

    Only stored successful snapshots have rows in etf_daily_holdings, so grouped
    row counts naturally exclude missing/zero-row dates. A missing table/path
    means the scraper is being used without an initialized DB, so validation is
    skipped.
    """
    db_path = getattr(db, "_DB_PATH", None)
    if db_path != ":memory:" and hasattr(db_path, "exists") and not db_path.exists():
        return None

    try:
        with db._connect() as conn:
            rows = conn.execute(
                """
                SELECT stock_count
                FROM (
                    SELECT date, COUNT(*) AS stock_count
                    FROM etf_daily_holdings
                    WHERE etf_code = ? AND source_type = ?
                    GROUP BY date
                    ORDER BY date DESC
                    LIMIT ?
                )
                """,
                (etf_code, source_type, limit),
            ).fetchall()
    except sqlite3.OperationalError:
        return None
    if not rows:
        return None
    return sum(row[0] for row in rows) / len(rows)


def _maybe_replace_low_row_count_sync(etf_code: str, moneydj_result: dict, official_candidate: dict | None = None) -> dict:
    validation = _row_count_validation(etf_code, moneydj_result)
    if not validation["low_confidence"]:
        return moneydj_result

    official_result = official_candidate or _official_fallback_static(etf_code)
    return _select_low_row_count_result(moneydj_result, official_result, validation)


async def _maybe_replace_low_row_count_async(
    etf_code: str,
    moneydj_result: dict,
    page,
    official_candidate: dict | None = None,
    target_date: date | None = None,
) -> dict:
    validation = _row_count_validation(etf_code, moneydj_result)
    if not validation["low_confidence"]:
        return moneydj_result

    official_result = official_candidate or await _official_fallback_with_browser(etf_code, page, target_date=target_date)
    return _select_low_row_count_result(moneydj_result, official_result, validation)


def _row_count_validation(etf_code: str, result: dict) -> dict:
    source_type = result.get("source_type") or ""
    current_stock_rows = len(result.get("stock_rows") or [])
    historical_mean = None
    minimum_expected = None
    low_confidence = False

    if source_type in _MONEYDJ_SOURCE_TYPES:
        historical_mean = get_historical_mean_stock_row_count(etf_code, source_type)
        if historical_mean:
            minimum_expected = historical_mean * _LOW_ROW_COUNT_THRESHOLD
            low_confidence = current_stock_rows < minimum_expected

    return {
        "low_confidence": low_confidence,
        "source_type": source_type,
        "moneydj_stock_rows": current_stock_rows,
        "historical_mean_stock_rows": historical_mean,
        "minimum_expected_stock_rows": minimum_expected,
        "threshold_ratio": _LOW_ROW_COUNT_THRESHOLD,
    }


def _select_low_row_count_result(moneydj_result: dict, official_result: dict, validation: dict) -> dict:
    moneydj_count = validation["moneydj_stock_rows"]
    if official_result.get("ok") is not True:
        return _with_row_count_warning(
            moneydj_result,
            validation,
            "low_row_count_official_fallback_failed",
            official_result=official_result,
        )

    official_count = len(official_result.get("stock_rows") or [])
    if official_count == 0:
        return _with_row_count_warning(
            moneydj_result,
            validation,
            "low_row_count_official_fallback_zero_rows",
            official_result=official_result,
        )

    stale_reason = _official_row_count_staleness_reason(moneydj_result, official_result)
    if stale_reason:
        return _with_row_count_warning(
            moneydj_result,
            validation,
            stale_reason,
            official_result=official_result,
        )

    if official_count == moneydj_count:
        return _with_row_count_warning(
            moneydj_result,
            validation,
            "low_row_count_confirmed_by_fallback",
            official_result=official_result,
        )

    if official_count > moneydj_count:
        minimum_expected = validation.get("minimum_expected_stock_rows") or 0.0
        if official_count >= minimum_expected:
            return official_result
        return _with_row_count_warning(
            official_result,
            validation,
            "low_row_count_official_fallback_still_low",
            official_result=official_result,
        )

    return _with_row_count_warning(
        moneydj_result,
        validation,
        "low_row_count_official_fallback_lower_row_count",
        official_result=official_result,
    )


def _official_row_count_staleness_reason(moneydj_result: dict, official_result: dict) -> str | None:
    moneydj_date = _result_data_date(moneydj_result)
    official_date = _result_data_date(official_result)
    if moneydj_date and official_date and official_date < moneydj_date:
        return "low_row_count_official_fallback_stale"
    if moneydj_date and official_date is None:
        return "low_row_count_official_fallback_missing_date"
    return None


def _with_row_count_warning(result: dict, validation: dict, reason: str, official_result: dict | None = None) -> dict:
    warning = {
        "reason": reason,
        "manual_inspection_required": True,
        "moneydj_stock_rows": validation["moneydj_stock_rows"],
        "official_stock_rows": len((official_result or {}).get("stock_rows") or []),
        "historical_mean_stock_rows": validation.get("historical_mean_stock_rows"),
        "minimum_expected_stock_rows": validation.get("minimum_expected_stock_rows"),
        "threshold_ratio": validation.get("threshold_ratio"),
    }
    if official_result:
        official_data_date = _result_data_date(official_result)
        if official_data_date:
            warning["official_data_date"] = official_data_date.isoformat()
        if official_result.get("ok") is not True:
            warning["official_error"] = official_result.get("reason", "unknown")
    moneydj_data_date = _result_data_date(result)
    if moneydj_data_date:
        warning["moneydj_data_date"] = moneydj_data_date.isoformat()

    return {
        **result,
        "warnings": [*(result.get("warnings") or []), reason],
        "manual_inspection_required": True,
        "row_count_warning": warning,
    }


def _result_data_date(result: dict):
    rows = result.get("all_rows") or result.get("stock_rows") or []
    for row in rows:
        parsed = _parse_row_date(row.get("date"))
        if parsed is not None:
            return parsed
    return None


def _is_stale_result(result: dict, target_date: date) -> bool:
    data_date = _result_data_date(result)
    return data_date is not None and data_date < target_date


def _is_fresh_result(result: dict, target_date: date) -> bool:
    return _result_data_date(result) == target_date


def _parse_row_date(value):
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


def _with_source_type(result: dict, source_type: str) -> dict:
    rows = []
    for row in result.get("all_rows", []) or []:
        rows.append({**row, "source_type": source_type})
    stock_rows = []
    for row in result.get("stock_rows", []) or []:
        stock_rows.append({**row, "source_type": source_type})
    non_stock_rows = []
    for row in result.get("non_stock_rows", []) or []:
        non_stock_rows.append({**row, "source_type": source_type})
    return {
        **result,
        "source_type": source_type,
        "all_rows": rows,
        "stock_rows": stock_rows,
        "non_stock_rows": non_stock_rows,
    }


def _normalize_source_result(result: dict, source_type: str) -> dict:
    return _apply_min_weight_gate(_with_source_type(result, source_type))


def _apply_min_weight_gate(result: dict, threshold: float = _MIN_WEIGHT_THRESHOLD) -> dict:
    """Normalize holdings and reject snapshots invalidated by the stock filter.

    `total_weight_all_rows` and `total_weight_stock_rows` describe only rows kept
    after the stock minimum-weight gate; they are not raw source-extracted totals.
    Non-stock rows remain untouched because derivatives may have zero or negative
    weights.
    """
    original_stock_rows = list(result.get("stock_rows", []) or [])
    stock_rows = [
        row for row in original_stock_rows
        if (row.get("weight_pct") or 0.0) >= threshold
    ]
    all_rows = [
        row for row in result.get("all_rows", []) or []
        if row.get("asset_type") != "stock" or (row.get("weight_pct") or 0.0) >= threshold
    ]
    non_stock_rows = list(result.get("non_stock_rows", []) or [])
    normalized = {
        **result,
        "all_rows": all_rows,
        "stock_rows": stock_rows,
        "non_stock_rows": non_stock_rows,
        "total_weight_all_rows": sum(row.get("weight_pct") or 0.0 for row in all_rows),
        "total_weight_stock_rows": sum(row.get("weight_pct") or 0.0 for row in stock_rows),
    }
    filter_changed_snapshot = (
        len(original_stock_rows) >= MIN_TAIWAN_STOCK_ROWS
        and len(stock_rows) < len(original_stock_rows)
    )
    if filter_changed_snapshot:
        valid, reason = validate_snapshot_rows([*stock_rows, *non_stock_rows])
        if not valid:
            return {
                **normalized,
                "ok": False,
                "reason": f"post_filter_invalid_snapshot:{reason}",
            }
    return normalized


def _run_async(coro) -> dict:
    """Run an async coroutine from sync code.

    This helper intentionally refuses to run inside an active event loop. In that
    case callers must use the native async API instead of nesting event loops.
    """
    if not isawaitable(coro):
        return coro

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)  # type: ignore[arg-type]

    raise RuntimeError(
        "scrape_holdings_with_browser cannot run an async browser scraper "
        "inside an active event loop; use scrape_holdings_with_browser_async instead"
    )
