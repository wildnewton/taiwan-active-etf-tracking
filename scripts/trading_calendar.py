"""Trading-day helpers backed by stock-market-data-update params.py.

The stock-market-data-update job updates its TW database later than the active
ETF job, so the database contents cannot gate the ETF scrape.  Instead we reuse
the same holiday list that the stock-market-data-update project uses to decide
whether Taiwan is trading today.
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
    holidays_by_year = _load_holidays(params_path)
    if holidays_by_year is None:
        return None

    day = run_date
    for _ in range(10):
        if _is_trading_day_with_holidays(day, holidays_by_year):
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


def _load_holidays(params_path: str | Path | None = None) -> Optional[dict]:
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
    return holidays if isinstance(holidays, dict) else None


def _is_trading_day_with_holidays(day: date, holidays_by_year: dict) -> bool:
    year_holidays = holidays_by_year.get(day.year, {})
    tw_holidays = year_holidays.get("TW", set()) if isinstance(year_holidays, dict) else set()
    return day.weekday() < 5 and day.strftime("%m%d") not in set(tw_holidays)


def _coerce_date(value) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return datetime.strptime(value, "%Y-%m-%d").date()
    if all(hasattr(value, attr) for attr in ("year", "month", "day", "isoformat")):
        return date(value.year, value.month, value.day)
    raise TypeError("value must be a date or ISO date string")
