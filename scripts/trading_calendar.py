"""Trading-day helpers backed by stock-market-data-update params.py.

The stock-market-data-update job updates its TW database later than the active
ETF job, so the database contents cannot gate the ETF scrape.  Instead we reuse
the same holiday and non-trading-day lists that the stock-market-data-update
project uses to decide whether Taiwan is trading today.
"""

import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional


DEFAULT_STOCK_PARAMS_PATH = Path(__file__).resolve().parents[2] / "stock-market-data-update" / "scripts" / "params.py"
STOCK_PARAMS_PATH_ENV = "STOCK_MARKET_PARAMS_PATH"


def stock_params_path() -> Path:
    configured = os.environ.get(STOCK_PARAMS_PATH_ENV)
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_STOCK_PARAMS_PATH


def latest_tw_trading_day_on_or_before(run_date, params_path: str | Path | None = None) -> Optional[date]:
    """Return the latest TW trading day on or before run_date.

    Returns None when params.py is unavailable or malformed.  Callers should
    treat None as "calendar unknown" rather than as a non-trading day.
    """
    run_date = _coerce_date(run_date)
    calendar = _load_calendar(params_path)
    if calendar is None:
        return None

    day = run_date
    for _ in range(10):
        if _is_trading_day_with_calendar(day, calendar):
            return day
        day -= timedelta(days=1)
    return None


def is_tw_trading_day(run_date, params_path: str | Path | None = None) -> Optional[bool]:
    """Return whether run_date is a TW trading day, or None if calendar is unknown."""
    run_date = _coerce_date(run_date)
    latest = latest_tw_trading_day_on_or_before(run_date, params_path=params_path)
    if latest is None:
        return None
    return latest == run_date


def _load_calendar(params_path: str | Path | None = None) -> Optional[dict]:
    path = Path(params_path).expanduser() if params_path is not None else stock_params_path()
    if not path.exists():
        return None

    namespace: dict = {"__file__": str(path)}
    try:
        code = path.read_text(encoding="utf-8")
        exec(compile(code, str(path), "exec"), namespace)
    except Exception:
        return None

    holidays = namespace.get("HOLIDAYS")
    if not isinstance(holidays, dict):
        return None

    non_trading_days = namespace.get("NON_TRADING_DAYS", {})
    if not isinstance(non_trading_days, dict):
        non_trading_days = {}

    return {"HOLIDAYS": holidays, "NON_TRADING_DAYS": non_trading_days}


def _is_trading_day_with_calendar(day: date, calendar: dict) -> bool:
    holidays_for_year = calendar["HOLIDAYS"].get(day.year, {})
    overrides_for_year = calendar["NON_TRADING_DAYS"].get(day.year, {})
    tw_holidays = holidays_for_year.get("TW", set()) if isinstance(holidays_for_year, dict) else set()
    tw_overrides = overrides_for_year.get("TW", set()) if isinstance(overrides_for_year, dict) else set()
    tw_non_trading_days = _coerce_mmdd_set(tw_holidays) | _coerce_mmdd_set(tw_overrides)
    return day.weekday() < 5 and day.strftime("%m%d") not in tw_non_trading_days


def _coerce_mmdd_set(value) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    return set(value)


def _coerce_date(value) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return datetime.strptime(value, "%Y-%m-%d").date()
    if all(hasattr(value, attr) for attr in ("year", "month", "day", "isoformat")):
        return date(value.year, value.month, value.day)
    raise TypeError("value must be a date or ISO date string")
