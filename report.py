"""Daily report generators for Taiwan Active ETF tracking."""
import json
import sqlite3
from datetime import datetime, timezone, timedelta

import db

CST = timezone(timedelta(hours=8))


SIGNAL_SECTIONS = [
    ("A. Strong consensus adds", lambda row: row["signal_type"] == "consensus_add_3d" and row["signal_strength"] == "strong"),
    ("B. New core positions", lambda row: row["signal_type"] == "new_core_position"),
    ("C. Consecutive accumulations", lambda row: row["signal_type"] == "consecutive_add_3d"),
    ("D. Consensus adds", lambda row: row["signal_type"] == "consensus_add_3d" and row["signal_strength"] != "strong"),
    ("E. Consensus reductions", lambda row: row["signal_type"] == "consensus_reduce_3d"),
    ("F. Consecutive reductions", lambda row: row["signal_type"] == "consecutive_reduce_3d"),
    ("G. Removed core positions", lambda row: row["signal_type"] == "removed_core_position"),
]


def generate_daily_report(summary: dict) -> str:
    """Generate a human-readable daily report from pipeline summary.

    Args:
        summary: dict from pipeline.run_daily_scrape() with keys:
            date, total_etfs, moneydj_success, official_success, failed,
            total_stock_rows, total_non_stock_rows, failures, results
    """
    now = datetime.now(CST)
    lines = [
        f"📊 台灣主動 ETF 每日持倉報告",
        f"📅 {now.strftime('%Y-%m-%d %H:%M')} CST",
        "",
        f"**數據日期**: {summary.get('date', 'N/A')}",
        f"**ETF 總數**: {summary['total_etfs']}",
        "",
        "**抓取結果**:",
        f"  ✅ MoneyDJ 成功: {summary['moneydj_success']}",
        f"  ✅ 官方網站成功: {summary['official_success']}",
        f"  ❌ 失敗: {summary['failed']}",
        "",
        f"**數據量**:",
        f"  股票持倉行數: {summary['total_stock_rows']}",
        f"  非股票資產行數: {summary['total_non_stock_rows']}",
    ]

    # Per-ETF completeness
    results = summary.get("results", [])
    if results:
        lines.append("")
        lines.append("**各 ETF 完整性**:")
        for r in results:
            status = "✅" if r["ok"] else "❌"
            weight = r.get("total_weight", 0)
            rows = r.get("stock_rows", 0)
            src = r.get("source_type", "").replace("_primary", "").replace("_fallback", "")
            lines.append(
                f"  {status} {r['etf_code']}: {rows}檔, {weight:.1f}% ({src})"
            )

    # Failures
    failures = summary.get("failures", [])
    if failures:
        lines.append("")
        lines.append("**失敗詳情**:")
        for f in failures:
            lines.append(f"  ❌ {f['etf_code']}: {f['reason']}")

    # Warnings
    warnings = []
    for r in results:
        if r["ok"]:
            weight = r.get("total_weight", 0)
            if weight < 80:
                warnings.append(f"  ⚠️ {r['etf_code']}: 持倉權重僅 {weight:.1f}%，可能不完整")
    if warnings:
        lines.append("")
        lines.append("**數據品質警告**:")
        lines.extend(warnings)

    return "\n".join(lines)


def get_latest_signal_date():
    """Return the latest date with manager signals, or None if unavailable."""
    conn = db._connect()
    try:
        row = conn.execute("SELECT MAX(date) FROM etf_manager_signals").fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row and row[0] else None


def generate_signal_report(signal_date=None) -> str:
    """Generate a grouped daily manager signal report."""
    signal_date = signal_date or get_latest_signal_date()
    lines = [
        "Taiwan Active ETF Manager Signals",
        f"Generated at: {datetime.now(CST).strftime('%Y-%m-%d %H:%M')} CST",
    ]

    if not signal_date:
        lines.append("No manager signals found.")
        return "\n".join(lines)

    rows = _load_signals(signal_date)
    lines.append(f"Latest signal date: {signal_date}")
    lines.append(f"Signals generated: {len(rows)}")

    if not rows:
        lines.append("No manager signals found.")
        return "\n".join(lines)

    for section_title, predicate in SIGNAL_SECTIONS:
        section_rows = [row for row in rows if predicate(row)]
        if not section_rows:
            continue
        lines.append("")
        lines.append(section_title)
        for row in sorted(section_rows, key=lambda item: abs(item["signal_score"]), reverse=True):
            lines.extend(_format_signal_row(row))

    return "\n".join(lines)


def _load_signals(signal_date):
    conn = db._connect()
    old_factory = conn.row_factory
    conn.row_factory = _dict_factory
    try:
        return conn.execute(
            """
            SELECT *
            FROM etf_manager_signals
            WHERE date = ?
            ORDER BY signal_type, signal_score DESC, stock_code
            """,
            (signal_date,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.row_factory = old_factory


def _format_signal_row(row):
    etf_codes = _decode_json_list(row.get("etf_codes"))
    issuers = _decode_json_list(row.get("issuers"))
    stock_display = f"{row['stock_code']} {row.get('stock_name') or ''}".strip()
    return [
        f"- {stock_display}",
        f"  Label: {row.get('action_label')}",
        f"  Score: {row.get('signal_score'):+.0f}",
        f"  Signal: {row.get('signal_type')} ({row.get('signal_strength')})",
        f"  ETFs: {', '.join(etf_codes)}",
        f"  Issuers: {', '.join(issuers)}",
    ]


def _decode_json_list(value):
    if not value:
        return []
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []
    return [str(item) for item in decoded]


def _dict_factory(cursor, row):
    return {column[0]: row[index] for index, column in enumerate(cursor.description)}
