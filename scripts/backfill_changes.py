import argparse
import json
from pathlib import Path

import db
from changes import detect_holding_changes
from manager_intent import generate_manager_intent_rollups
from signals import generate_manager_signals


def holding_dates(from_date=None, to_date=None):
    """Return sorted holding dates, optionally filtered by date range."""
    query = "SELECT DISTINCT date FROM etf_daily_holdings"
    params = []
    filters = []
    if from_date:
        filters.append("date >= ?")
        params.append(from_date)
    if to_date:
        filters.append("date <= ?")
        params.append(to_date)
    if filters:
        query += " WHERE " + " AND ".join(filters)
    query += " ORDER BY date"

    with db._connect() as conn:
        return [row[0] for row in conn.execute(query, params).fetchall()]


def _all_holding_dates():
    with db._connect() as conn:
        return [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT date FROM etf_daily_holdings ORDER BY date"
            ).fetchall()
        ]


def backfill_changes(
    from_date=None,
    to_date=None,
    regenerate_signals=False,
    regenerate_manager_intent=False,
    all_derived=False,
):
    """Recompute historical holding changes and optionally derived layers.

    The previous date is taken from the full holdings history, not just the
    requested date range, so a range starting mid-history can still compare
    against the immediately preceding holdings date.
    """
    if all_derived:
        regenerate_manager_intent = True
        regenerate_signals = True

    all_dates = _all_holding_dates()
    requested_dates = set(holding_dates(from_date=from_date, to_date=to_date))

    processed_dates = []
    skipped_first_dates = []
    skipped_etfs_by_date = {}
    manager_intent_dates = []
    signal_dates = []
    total_change_rows = 0
    total_manager_intent_rows = 0
    total_signal_rows = 0

    for index, current_date in enumerate(all_dates):
        if current_date not in requested_dates:
            continue
        if index == 0:
            skipped_first_dates.append(current_date)
            continue

        previous_date = all_dates[index - 1]
        summary = detect_holding_changes(current_date, previous_date)
        skipped_etfs = summary.get("skipped_etfs") or []
        if skipped_etfs:
            skipped_etfs_by_date[current_date] = skipped_etfs

        if summary.get("ok"):
            processed_dates.append(current_date)
            total_change_rows += summary.get("rows", 0)
            if regenerate_manager_intent:
                intent_summary = generate_manager_intent_rollups(current_date)
                if intent_summary.get("ok"):
                    manager_intent_dates.append(current_date)
                    total_manager_intent_rows += intent_summary.get("rows", 0)
            if regenerate_signals:
                signal_summary = generate_manager_signals(current_date)
                if signal_summary.get("ok"):
                    signal_dates.append(current_date)
                    total_signal_rows += signal_summary.get("signals", 0)

    return {
        "ok": True,
        "from_date": from_date,
        "to_date": to_date,
        "processed_dates": processed_dates,
        "skipped_first_dates": skipped_first_dates,
        "skipped_etfs_by_date": skipped_etfs_by_date,
        "change_rows": total_change_rows,
        "manager_intent_dates": manager_intent_dates,
        "manager_intent_rows": total_manager_intent_rows,
        "regenerated_signal_dates": signal_dates,
        "signal_rows": total_signal_rows,
    }


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Backfill ETF holding changes and optionally derived manager-intent/signals."
    )
    parser.add_argument(
        "--db",
        dest="db_path",
        default=str(db.DEFAULT_DB_PATH),
        help="SQLite database path. Defaults to data/active_etf_holdings.sqlite.",
    )
    parser.add_argument("--from-date", dest="from_date")
    parser.add_argument("--to-date", dest="to_date")
    parser.add_argument(
        "--regenerate-manager-intent",
        action="store_true",
        help="Regenerate manager_intent_rollups after recomputing changes.",
    )
    parser.add_argument(
        "--regenerate-signals",
        action="store_true",
        help="Regenerate etf_manager_signals after recomputing changes.",
    )
    parser.add_argument(
        "--all-derived",
        action="store_true",
        help="Regenerate all derived layers after recomputing changes.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    db.init_db(Path(args.db_path))
    summary = backfill_changes(
        from_date=args.from_date,
        to_date=args.to_date,
        regenerate_manager_intent=args.regenerate_manager_intent,
        regenerate_signals=args.regenerate_signals,
        all_derived=args.all_derived,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
