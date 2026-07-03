"""DB-backed ETF universe helpers.

`etf_universe` is the operational source of truth for which Taiwan active ETFs
should be fetched. Rows with `retired = 0` are included in nightly holdings
scrapes; retired rows are retained for historical lookup but skipped going
forward.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import db
from config import get_moneydj_url


SEED_PATH = Path(__file__).resolve().parents[1] / "data" / "seeds" / "etf_universe_seed.json"
_SCOPE_EXCLUSION_MARKERS = (
    "excluded_from_taiwan_stock_universe",
    "trades_offshore_instruments=true",
)


def _now() -> str:
    return datetime.now().isoformat()


def _today() -> str:
    return date.today().isoformat()


def _dict_factory(cursor, row):
    return {column[0]: row[index] for index, column in enumerate(cursor.description)}


def _ensure_table() -> None:
    conn = db._connect()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS etf_universe (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            issuer TEXT,
            market TEXT,
            isin TEXT,
            retired INTEGER NOT NULL DEFAULT 0,
            first_seen_date TEXT,
            last_seen_date TEXT,
            retired_since TEXT,
            pending_retirement_since TEXT,
            official_url TEXT,
            official_method TEXT,
            official_logic TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    _ensure_pending_retirement_column(conn)


def _ensure_pending_retirement_column(conn) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(etf_universe)").fetchall()}
    if "pending_retirement_since" not in existing:
        conn.execute("ALTER TABLE etf_universe ADD COLUMN pending_retirement_since TEXT")


def _is_scope_excluded(row: dict) -> bool:
    official_logic = row.get("official_logic") or ""
    return any(marker in official_logic for marker in _SCOPE_EXCLUSION_MARKERS)


def _with_derived_fields(row: dict) -> dict:
    return {**row, "moneydj_url": get_moneydj_url(row["code"])}


def _fetch_raw(code: str) -> dict | None:
    _ensure_table()
    conn = db._connect()
    old = conn.row_factory
    conn.row_factory = _dict_factory
    try:
        return conn.execute(
            "SELECT * FROM etf_universe WHERE code = ?",
            (code.upper(),),
        ).fetchone()
    finally:
        conn.row_factory = old


def _count_rows() -> int:
    _ensure_table()
    conn = db._connect()
    old = conn.row_factory
    conn.row_factory = None
    try:
        row = conn.execute("SELECT COUNT(*) FROM etf_universe").fetchone()
    finally:
        conn.row_factory = old
    return row[0] if row else 0


def seed_etf_universe_from_file(path: str | Path | None = None, seen_date: str | None = None) -> int:
    """Seed known ETF metadata from JSON without overwriting DB edits.

    Returns the number of newly inserted rows. Existing rows are intentionally
    left untouched so manual DB metadata edits are preserved.
    """
    _ensure_table()
    seed_path = Path(path) if path is not None else SEED_PATH
    rows = json.loads(seed_path.read_text(encoding="utf-8"))
    inserted = 0
    seen_date = seen_date or _today()
    now = _now()

    with db._connect() as conn:
        for raw in rows:
            code = raw["code"].upper()
            existing = conn.execute(
                "SELECT 1 FROM etf_universe WHERE code = ?",
                (code,),
            ).fetchone()
            if existing:
                continue
            conn.execute(
                """
                INSERT INTO etf_universe (
                    code, name, issuer, market, isin, retired,
                    first_seen_date, last_seen_date, retired_since,
                    pending_retirement_since,
                    official_url, official_method, official_logic,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, NULL, NULL, ?, ?, ?, ?, ?)
                """,
                (
                    code,
                    raw["name"],
                    raw.get("issuer"),
                    raw.get("market"),
                    raw.get("isin"),
                    seen_date,
                    seen_date,
                    raw.get("official_url"),
                    raw.get("official_method"),
                    raw.get("official_logic"),
                    now,
                    now,
                ),
            )
            inserted += 1
    return inserted


def ensure_seeded() -> int:
    """Seed the universe only when it is currently empty."""
    if _count_rows() > 0:
        return 0
    return seed_etf_universe_from_file()


def get_active_etfs() -> list[dict]:
    ensure_seeded()
    conn = db._connect()
    old = conn.row_factory
    conn.row_factory = _dict_factory
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM etf_universe
            WHERE retired = 0
            ORDER BY code
            """
        ).fetchall()
    finally:
        conn.row_factory = old
    return [_with_derived_fields(row) for row in rows]


def get_active_etf_count() -> int:
    ensure_seeded()
    conn = db._connect()
    old = conn.row_factory
    conn.row_factory = None
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM etf_universe WHERE retired = 0"
        ).fetchone()
    finally:
        conn.row_factory = old
    return row[0] if row else 0


def get_etf_config(code: str) -> dict:
    ensure_seeded()
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

    if existing:
        merged = {**existing, **row, "code": code, "updated_at": now}
    else:
        merged = {
            "code": code,
            "name": row.get("name") or code,
            "issuer": row.get("issuer"),
            "market": row.get("market"),
            "isin": row.get("isin"),
            "retired": int(row.get("retired", 0)),
            "first_seen_date": row.get("first_seen_date"),
            "last_seen_date": row.get("last_seen_date"),
            "retired_since": row.get("retired_since"),
            "pending_retirement_since": row.get("pending_retirement_since"),
            "official_url": row.get("official_url"),
            "official_method": row.get("official_method"),
            "official_logic": row.get("official_logic"),
            "created_at": now,
            "updated_at": now,
        }

    with db._connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO etf_universe (
                code, name, issuer, market, isin, retired,
                first_seen_date, last_seen_date, retired_since,
                pending_retirement_since,
                official_url, official_method, official_logic,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                merged["code"],
                merged["name"],
                merged.get("issuer"),
                merged.get("market"),
                merged.get("isin"),
                int(merged.get("retired") or 0),
                merged.get("first_seen_date"),
                merged.get("last_seen_date"),
                merged.get("retired_since"),
                merged.get("pending_retirement_since"),
                merged.get("official_url"),
                merged.get("official_method"),
                merged.get("official_logic"),
                merged.get("created_at") or now,
                merged.get("updated_at") or now,
            ),
        )


def retire_etf(code: str, retired_since: str | None = None, reason: str | None = None) -> None:
    ensure_seeded()
    retired_since = retired_since or _today()
    now = _now()
    with db._connect() as conn:
        conn.execute(
            """
            UPDATE etf_universe
            SET retired = 1,
                retired_since = COALESCE(retired_since, ?),
                pending_retirement_since = NULL,
                updated_at = ?
            WHERE code = ?
            """,
            (retired_since, now, code.upper()),
        )


def _reactivate_etf(code: str, seen_date: str) -> None:
    now = _now()
    with db._connect() as conn:
        conn.execute(
            """
            UPDATE etf_universe
            SET retired = 0,
                retired_since = NULL,
                pending_retirement_since = NULL,
                last_seen_date = ?,
                updated_at = ?
            WHERE code = ?
            """,
            (seen_date, now, code.upper()),
        )


def reconcile_discovered_universe(
    discovered_rows: list[dict],
    seen_date: str | None = None,
    discovery_complete: bool = True,
) -> dict:
    """Reconcile exchange-discovered active ETFs into etf_universe.

    Discovery rows are expected to represent currently listed active A-class ETFs.
    Missing active rows are retired only after they are absent from two complete
    discovery runs. Incomplete discovery runs can insert/update observed ETFs
    but never start or complete retirement.
    """
    ensure_seeded()
    seen_date = seen_date or _today()
    discovered = {row["code"].upper(): {**row, "code": row["code"].upper()} for row in discovered_rows}
    inserted: list[str] = []
    reactivated: list[str] = []
    updated: list[str] = []
    pending_retirement: list[str] = []
    retirement_skipped: list[str] = []
    retired: list[str] = []
    now = _now()

    with db._connect() as conn:
        current_rows = conn.execute(
            """
            SELECT code, retired, pending_retirement_since, official_logic
            FROM etf_universe
            """
        ).fetchall()
        known = {
            row[0]: {
                "retired": row[1],
                "pending_retirement_since": row[2],
                "official_logic": row[3],
            }
            for row in current_rows
        }

        for code, raw in sorted(discovered.items()):
            if code not in known:
                conn.execute(
                    """
                    INSERT INTO etf_universe (
                        code, name, issuer, market, isin, retired,
                        first_seen_date, last_seen_date, retired_since,
                        pending_retirement_since,
                        official_url, official_method, official_logic,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, NULL, NULL, NULL, NULL, NULL, ?, ?)
                    """,
                    (
                        code,
                        raw.get("name") or code,
                        raw.get("issuer"),
                        raw.get("market"),
                        raw.get("isin"),
                        seen_date,
                        seen_date,
                        now,
                        now,
                    ),
                )
                inserted.append(code)
                continue

            if known[code]["retired"]:
                if _is_scope_excluded(known[code]):
                    conn.execute(
                        """
                        UPDATE etf_universe
                        SET last_seen_date = ?,
                            pending_retirement_since = NULL,
                            market = COALESCE(?, market),
                            isin = COALESCE(?, isin),
                            updated_at = ?
                        WHERE code = ?
                        """,
                        (seen_date, raw.get("market"), raw.get("isin"), now, code),
                    )
                    continue
                conn.execute(
                    """
                    UPDATE etf_universe
                    SET retired = 0,
                        retired_since = NULL,
                        pending_retirement_since = NULL,
                        last_seen_date = ?,
                        market = COALESCE(?, market),
                        isin = COALESCE(?, isin),
                        updated_at = ?
                    WHERE code = ?
                    """,
                    (seen_date, raw.get("market"), raw.get("isin"), now, code),
                )
                reactivated.append(code)
            else:
                conn.execute(
                    """
                    UPDATE etf_universe
                    SET last_seen_date = ?,
                        pending_retirement_since = NULL,
                        market = COALESCE(?, market),
                        isin = COALESCE(?, isin),
                        updated_at = ?
                    WHERE code = ?
                    """,
                    (seen_date, raw.get("market"), raw.get("isin"), now, code),
                )
                updated.append(code)

        discovered_codes = set(discovered)
        for code, state in sorted(known.items()):
            if state["retired"]:
                continue
            if code in discovered_codes:
                continue
            if not discovery_complete:
                retirement_skipped.append(code)
                continue
            pending_since = state.get("pending_retirement_since")
            if pending_since and pending_since != seen_date:
                conn.execute(
                    """
                    UPDATE etf_universe
                    SET retired = 1,
                        retired_since = COALESCE(retired_since, ?),
                        pending_retirement_since = NULL,
                        updated_at = ?
                    WHERE code = ?
                    """,
                    (seen_date, now, code),
                )
                retired.append(code)
            else:
                conn.execute(
                    """
                    UPDATE etf_universe
                    SET pending_retirement_since = COALESCE(pending_retirement_since, ?),
                        updated_at = ?
                    WHERE code = ?
                    """,
                    (seen_date, now, code),
                )
                pending_retirement.append(code)

    return {
        "inserted": inserted,
        "reactivated": reactivated,
        "updated": updated,
        "pending_retirement": pending_retirement,
        "retirement_skipped": retirement_skipped,
        "retired": retired,
        "active_total": get_active_etf_count(),
    }
