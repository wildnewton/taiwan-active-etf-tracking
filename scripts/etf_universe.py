"""DB-backed ETF universe helpers.

`etf_universe` is the operational source of truth for which Taiwan active ETFs
should be fetched. Holdings history provides retirement candidates and the
historical cutoff for manually retired ETFs.
"""
from __future__ import annotations

from datetime import date, datetime

import db
from config import get_moneydj_url


_SCOPE_EXCLUSION_MARKERS = (
    "excluded_from_taiwan_stock_universe",
    "trades_offshore_instruments=true",
)
_ETF_SELECT_COLUMNS = """
    code, name, issuer, market, isin, listing_date, retired,
    first_seen_date, official_url, official_method, official_logic,
    created_at, updated_at
"""


def _now() -> str:
    return datetime.now().isoformat()


def _today() -> str:
    return date.today().isoformat()


def _as_date_text(value: date | datetime | str | None) -> str:
    if value is None:
        return _today()
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _dict_factory(cursor, row):
    return {column[0]: row[index] for index, column in enumerate(cursor.description)}


def _ensure_table() -> None:
    conn = db._connect()
    with conn:
        db._create_etf_universe_table(conn)
        db._ensure_etf_universe_columns(conn)


def _is_scope_excluded(row: dict) -> bool:
    official_logic = row.get("official_logic") or ""
    return any(marker in official_logic for marker in _SCOPE_EXCLUSION_MARKERS)


def _is_listed_on(row: dict, as_of_date: str) -> bool:
    listing_date = row.get("listing_date")
    return listing_date is None or listing_date <= as_of_date


def _is_eligible_on(
    row: dict,
    as_of_date: str,
    latest_holdings_date: str | None,
) -> bool:
    if _is_scope_excluded(row) or not _is_listed_on(row, as_of_date):
        return False
    if not row.get("retired"):
        return True
    return latest_holdings_date is not None and as_of_date <= latest_holdings_date


def _with_derived_fields(row: dict) -> dict:
    return {**row, "moneydj_url": get_moneydj_url(row["code"])}


def _fetch_raw(code: str) -> dict | None:
    conn = db._connect()
    old = conn.row_factory
    conn.row_factory = _dict_factory
    try:
        return conn.execute(
            f"SELECT {_ETF_SELECT_COLUMNS} FROM etf_universe WHERE code = ?",
            (code.upper(),),
        ).fetchone()
    finally:
        conn.row_factory = old


def _latest_holdings_dates(conn) -> dict[str, str]:
    rows = conn.execute(
        """
        SELECT etf_code, MAX(date)
        FROM (
            SELECT etf_code, date FROM etf_daily_holdings
            UNION ALL
            SELECT etf_code, date FROM etf_daily_non_stock_assets
        )
        GROUP BY etf_code
        """
    ).fetchall()
    return {etf_code: latest_date for etf_code, latest_date in rows}


def _latest_usable_holdings_dates(conn, as_of_date: str, limit: int = 2) -> list[str]:
    rows = conn.execute(
        """
        SELECT date
        FROM (
            SELECT date FROM etf_daily_holdings WHERE date <= ?
            UNION
            SELECT date FROM etf_daily_non_stock_assets WHERE date <= ?
        )
        ORDER BY date DESC
        LIMIT ?
        """,
        (as_of_date, as_of_date, limit),
    ).fetchall()
    return [row[0] for row in rows]


def _etf_codes_with_holdings_on_dates(conn, data_dates: list[str]) -> set[str]:
    if not data_dates:
        return set()
    placeholders = ",".join("?" for _ in data_dates)
    rows = conn.execute(
        f"""
        SELECT etf_code
        FROM (
            SELECT etf_code FROM etf_daily_holdings
            WHERE date IN ({placeholders})
            UNION
            SELECT etf_code FROM etf_daily_non_stock_assets
            WHERE date IN ({placeholders})
        )
        """,
        [*data_dates, *data_dates],
    ).fetchall()
    return {row[0] for row in rows}


def get_active_etfs(
    as_of_date: date | datetime | str | None = None,
) -> list[dict]:
    as_of_date = _as_date_text(as_of_date)
    conn = db._connect()
    old = conn.row_factory
    conn.row_factory = _dict_factory
    try:
        rows = conn.execute(
            f"""
            SELECT {_ETF_SELECT_COLUMNS}
            FROM etf_universe
            WHERE retired = 0
              AND (listing_date IS NULL OR listing_date <= ?)
            ORDER BY code
            """,
            (as_of_date,),
        ).fetchall()
    finally:
        conn.row_factory = old
    return [
        _with_derived_fields(row)
        for row in rows
        if not _is_scope_excluded(row)
    ]


def get_active_etf_count(
    as_of_date: date | datetime | str | None = None,
) -> int:
    return len(get_active_etfs(as_of_date))


def get_eligible_etf_codes(
    as_of_date: date | datetime | str,
) -> list[str]:
    """Return ETFs that belonged to the analysis universe on one date."""
    as_of_date = _as_date_text(as_of_date)
    conn = db._connect()
    old = conn.row_factory
    conn.row_factory = _dict_factory
    try:
        rows = conn.execute(
            f"SELECT {_ETF_SELECT_COLUMNS} FROM etf_universe ORDER BY code"
        ).fetchall()
    finally:
        conn.row_factory = old
    latest_dates = _latest_holdings_dates(conn)
    return [
        row["code"]
        for row in rows
        if _is_eligible_on(row, as_of_date, latest_dates.get(row["code"]))
    ]


def get_eligible_etf_count(
    as_of_date: date | datetime | str,
) -> int:
    return len(get_eligible_etf_codes(as_of_date))


def get_etf_config(code: str) -> dict:
    row = _fetch_raw(code)
    if row is None:
        raise KeyError(f"Unknown ETF code: {code}")
    return _with_derived_fields(row)


def upsert_etf(row: dict) -> None:
    """Insert or update one ETF universe row, preserving omitted fields."""
    _ensure_table()
    code = row["code"].upper()
    existing = _fetch_raw(code)
    now = _now()
    normalized = {
        key: value
        for key, value in row.items()
        if key not in {
            "last_active_date",
            "last_seen_date",
            "retired_since",
            "pending_retirement_since",
        }
    }

    if existing:
        merged = {**existing, **normalized, "code": code, "updated_at": now}
    else:
        merged = {
            "code": code,
            "name": normalized.get("name") or code,
            "issuer": normalized.get("issuer"),
            "market": normalized.get("market"),
            "isin": normalized.get("isin"),
            "listing_date": normalized.get("listing_date"),
            "retired": int(normalized.get("retired", 0)),
            "first_seen_date": normalized.get("first_seen_date"),
            "official_url": normalized.get("official_url"),
            "official_method": normalized.get("official_method"),
            "official_logic": normalized.get("official_logic"),
            "created_at": now,
            "updated_at": now,
        }

    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_universe (
                code, name, issuer, market, isin, listing_date, retired,
                first_seen_date, official_url, official_method, official_logic,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                name = excluded.name,
                issuer = excluded.issuer,
                market = excluded.market,
                isin = excluded.isin,
                listing_date = excluded.listing_date,
                retired = excluded.retired,
                first_seen_date = excluded.first_seen_date,
                official_url = excluded.official_url,
                official_method = excluded.official_method,
                official_logic = excluded.official_logic,
                updated_at = excluded.updated_at
            """,
            (
                merged["code"],
                merged["name"],
                merged.get("issuer"),
                merged.get("market"),
                merged.get("isin"),
                merged.get("listing_date"),
                int(merged.get("retired") or 0),
                merged.get("first_seen_date"),
                merged.get("official_url"),
                merged.get("official_method"),
                merged.get("official_logic"),
                merged.get("created_at") or now,
                merged.get("updated_at") or now,
            ),
        )


def retire_etf(
    code: str,
    last_active_date: str | None = None,
    reason: str | None = None,
    retired_since: str | None = None,
) -> None:
    """Mark an ETF retired after manual confirmation.

    Legacy date arguments remain accepted for callers but no longer affect state.
    """
    del last_active_date, reason, retired_since
    with db._connect() as conn:
        conn.execute(
            """
            UPDATE etf_universe
            SET retired = 1, updated_at = ?
            WHERE code = ?
            """,
            (_now(), code.upper()),
        )


def reconcile_discovered_universe(
    discovered_rows: list[dict],
    seen_date: str | None = None,
    discovery_complete: bool = True,
) -> dict:
    """Reconcile discovery metadata and report manual retirement candidates.

    Discovery may add ETFs and refresh neutral metadata, but it never changes the
    manually confirmed ``retired`` status. Candidate status is derived from the
    current complete discovery and the two latest usable holdings dates.
    """
    seen_date = seen_date or _today()
    discovered = {
        row["code"].upper(): {**row, "code": row["code"].upper()}
        for row in discovered_rows
    }
    inserted: list[str] = []
    updated: list[str] = []
    retirement_candidates: list[str] = []
    retirement_skipped: list[str] = []
    now = _now()

    with db._connect() as conn:
        old = conn.row_factory
        conn.row_factory = _dict_factory
        try:
            current_rows = conn.execute(
                f"SELECT {_ETF_SELECT_COLUMNS} FROM etf_universe ORDER BY code"
            ).fetchall()
        finally:
            conn.row_factory = old
        known = {row["code"]: row for row in current_rows}

        for code, raw in sorted(discovered.items()):
            if code not in known:
                conn.execute(
                    """
                    INSERT INTO etf_universe (
                        code, name, issuer, market, isin, listing_date, retired,
                        first_seen_date, official_url, official_method, official_logic,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, NULL, NULL, NULL, ?, ?)
                    """,
                    (
                        code,
                        raw.get("name") or code,
                        raw.get("issuer"),
                        raw.get("market"),
                        raw.get("isin"),
                        raw.get("listing_date"),
                        seen_date,
                        now,
                        now,
                    ),
                )
                inserted.append(code)
                continue

            conn.execute(
                """
                UPDATE etf_universe
                SET market = COALESCE(?, market),
                    isin = COALESCE(?, isin),
                    listing_date = COALESCE(?, listing_date),
                    updated_at = ?
                WHERE code = ?
                """,
                (
                    raw.get("market"),
                    raw.get("isin"),
                    raw.get("listing_date"),
                    now,
                    code,
                ),
            )
            updated.append(code)

        discovered_codes = set(discovered)
        missing_rows = [
            row
            for row in current_rows
            if not row.get("retired") and row["code"] not in discovered_codes
        ]
        if not discovery_complete:
            retirement_skipped = sorted(row["code"] for row in missing_rows)
        else:
            recent_dates = _latest_usable_holdings_dates(conn, seen_date, limit=2)
            if len(recent_dates) == 2:
                older_date = recent_dates[-1]
                present_codes = _etf_codes_with_holdings_on_dates(conn, recent_dates)
                retirement_candidates = sorted(
                    row["code"]
                    for row in missing_rows
                    if not _is_scope_excluded(row)
                    and _is_listed_on(row, older_date)
                    and row["code"] not in present_codes
                )

    return {
        "inserted": inserted,
        "reactivated": [],
        "updated": updated,
        "pending_retirement": [],
        "retirement_skipped": retirement_skipped,
        "retired": [],
        "retirement_candidates": retirement_candidates,
        "active_total": get_active_etf_count(as_of_date=seen_date),
    }
