"""Daily report generators for Taiwan Active ETF tracking."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

import db
from changes import _select_canonical_sources
from etf_universe import get_active_etf_count

CST = timezone(timedelta(hours=8))
_MATERIAL_POSITION_WEIGHT = 0.5
_TOP_RANK_CUTOFF = 20

FRESHNESS_ORDER = {
    "new": 0,
    "reversal": 1,
    "persistent": 2,
    "current": 3,
    "fading": 4,
    "stale": 5,
}


def generate_signal_report(signal_date=None) -> str:
    now = datetime.now(CST)
    data_date = signal_date or _get_latest_holdings_date()
    prev_date = _get_previous_holdings_date(data_date)

    lines = [
        "📊 台灣主動 ETF 每日報告",
        f"📅 {now.strftime('%Y-%m-%d %H:%M')} CST | 資料日期: {data_date or 'N/A'}",
        "",
    ]

    quality = _get_data_quality(data_date)
    lines.extend(_render_data_quality(quality))

    stats = _get_summary_stats(data_date)
    changes = _get_change_summary(data_date)
    signals = _get_signals(data_date)

    lines.append("═══ 摘要 ═══")
    lines.append(f"ETF 數量: {stats['etf_count']} | 股票檔數: {stats['stock_count']} | 非股票資產: {stats['non_stock_count']}")
    if changes:
        lines.append(
            f"較前日 ({prev_date or 'N/A'}): "
            f"🟢 {changes['new_count']} 新增 | 🔴 {changes['removed_count']} 移除 | "
            f"📊 {changes['increased_count']} 權重上升 | 📊 {changes['decreased_count']} 權重下降"
        )
    if signals:
        summary = _signal_summary(signals)
        lines.append(
            f"今日訊號: 🔥 fresh {summary['fresh_consensus']} | 🔁 reversal {summary['reversals']} | "
            f"📈 persistent {summary['persistent_consensus']} | 🧊 stale/fading {summary['stale_or_fading']}"
        )
    lines.append("")

    if signals:
        lines.extend(_render_manager_signals(signals))

    top_movers = _get_top_movers(data_date)
    lines.extend(_render_exposure_movers(top_movers))

    new_positions = _get_new_positions(data_date)
    removed_positions = _get_removed_positions(data_date)
    hidden_counts = _get_hidden_position_counts(data_date)
    lines.extend(_render_new_removed_positions(new_positions, removed_positions, hidden_counts))

    consensus = _get_consensus_stocks(data_date, min_etfs=15)
    lines.extend(_render_consensus_holdings(consensus, data_date, prev_date))

    return "\n".join(lines).rstrip()


def _render_data_quality(quality: dict) -> list[str]:
    lines = ["═══ 資料品質 / 信任度 ═══"]
    lines.append(f"資料品質: {quality['status_label']}")
    if quality["expected_count"]:
        lines.append(f"Active ETF universe: {quality['expected_count']} | 成功持倉 ETF: {quality['actual_count']}/{quality['expected_count']}")
    else:
        lines.append(f"成功持倉 ETF: {quality['actual_count']}")
    if quality["failed_etfs"]:
        lines.append(f"抓取失敗: {', '.join(quality['failed_etfs'])}")
    if quality["change_skips"]:
        lines.append("變更偵測跳過:")
        for row in quality["change_skips"]:
            current_source = row.get("current_source_type") or "None"
            previous_source = row.get("previous_source_type") or "None"
            lines.append(f"  {row['etf_code']} {row.get('reason') or 'unknown'} ({current_source}→{previous_source})")
    if quality["warnings"]:
        lines.append("資料品質警告:")
        for warning in quality["warnings"]:
            lines.append(f"  {warning}")
    else:
        lines.append("資料品質警告: 無")
    lines.append("")
    return lines


def _render_manager_signals(signals: list[dict]) -> list[str]:
    sections = [
        ("🔥 Fresh consensus", lambda row: _is_consensus(row) and _freshness(row) == "new"),
        ("🔁 Reversals", lambda row: _freshness(row) == "reversal"),
        ("📈 Persistent consensus", lambda row: _is_consensus(row) and _freshness(row) == "persistent"),
        ("🧊 Stale / fading consensus", lambda row: _is_consensus(row) and _freshness(row) in {"stale", "fading"}),
        ("📌 Single-ETF / current active signals", lambda row: not _is_consensus(row) or _freshness(row) == "current"),
    ]
    lines = ["═══ 📈 管理人訊號（按新鮮度） ═══"]
    emitted = set()
    for title, predicate in sections:
        rows = [row for row in signals if predicate(row) and row.get("signal_id") not in emitted]
        if not rows:
            continue
        lines.append(title)
        for row in sorted(rows, key=_signal_sort_key)[:8]:
            emitted.add(row.get("signal_id"))
            lines.append(f"  {_format_signal_line(row)}")
    if not emitted:
        lines.append("  無可排序的管理人訊號")
    lines.append("")
    return lines


def _render_exposure_movers(top_movers: list[dict]) -> list[str]:
    lines = ["═══ 📊 Exposure movers（權重變動，不等於經理人交易） ═══"]
    if not top_movers:
        lines.append("  無重大權重變動資料")
    else:
        for row in top_movers:
            arrow = "📈" if (row.get("weight_delta_1d") or 0) > 0 else "📉"
            lines.append(
                f"  {arrow} {row['stock_code']} {row.get('stock_name') or ''} [{row['etf_code']}] "
                f"{_fmt_pct(row.get('prev_weight'))} → {_fmt_pct(row.get('curr_weight'))} "
                f"(權重Δ{_fmt_signed(row.get('weight_delta_1d'))}) | "
                f"{row.get('position_change_type') or 'unknown'} | conf={row.get('confidence') or 'normal'}"
            )
    lines.append("")
    return lines


def _render_new_removed_positions(new_positions, removed_positions, hidden_counts):
    lines = []
    if new_positions or hidden_counts["new"]:
        lines.append("═══ 🆕 重要新增部位 ═══")
        for code, entries in _group_positions(new_positions).items():
            lines.append(f"  ➕ {code} {entries[0].get('stock_name') or ''} 最大 {max(e['weight_pct'] for e in entries):.2f}% ({', '.join(e['etf_code'] for e in entries)})")
        if hidden_counts["new"]:
            lines.append(f"  低權重新增已隱藏: {hidden_counts['new']}")
        lines.append("")
    if removed_positions or hidden_counts["removed"]:
        lines.append("═══ ❌ 重要移除部位 ═══")
        for code, entries in _group_positions(removed_positions).items():
            lines.append(f"  ➖ {code} {entries[0].get('stock_name') or ''} 原 {max(e['prev_weight_pct'] for e in entries):.2f}% ({', '.join(e['etf_code'] for e in entries)})")
        if hidden_counts["removed"]:
            lines.append(f"  低權重移除已隱藏: {hidden_counts['removed']}")
        lines.append("")
    return lines


def _render_consensus_holdings(consensus, data_date, prev_date):
    if not consensus:
        return []
    lines = ["═══ 📊 高共識持股快照（不是買賣訊號） ═══"]
    for row in consensus:
        delta = _get_stock_weight_change(row["stock_code"], data_date, prev_date) if prev_date else None
        delta_text = f" | 總權重Δ{delta:+.2f}%" if delta is not None else ""
        lines.append(
            f"  {row['stock_code']} {row.get('stock_name') or ''} "
            f"{row['etf_count']}/{row.get('active_etf_count') or '?'}檔 | "
            f"平均 {row['avg_weight']:.2f}% | 最高 {row['max_weight']:.2f}%{delta_text}"
        )
    lines.append("")
    return lines


@contextmanager
def _using_row_factory(factory):
    conn = db._connect()
    old_factory = conn.row_factory
    conn.row_factory = factory
    try:
        yield conn
    finally:
        conn.row_factory = old_factory


def _dict_factory(cursor, row):
    return {column[0]: row[index] for index, column in enumerate(cursor.description)}


def _canonical_rows(data_date):
    if not data_date:
        return []
    try:
        selected = _select_canonical_sources(data_date)
        with _using_row_factory(_dict_factory) as conn:
            rows = conn.execute("SELECT * FROM etf_daily_holdings WHERE date = ?", (data_date,)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [row for row in rows if row.get("source_type") == selected.get(row.get("etf_code"), {}).get("source_type")]


def _canonical_stock_rows(data_date):
    return [row for row in _canonical_rows(data_date) if row.get("asset_type") == "stock"]


def _get_latest_holdings_date():
    try:
        with _using_row_factory(None) as conn:
            row = conn.execute("SELECT MAX(date) FROM etf_daily_holdings").fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row and row[0] else None


def _get_previous_holdings_date(current_date):
    if not current_date:
        return None
    try:
        with _using_row_factory(None) as conn:
            row = conn.execute("SELECT MAX(date) FROM etf_daily_holdings WHERE date < ?", (current_date,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row and row[0] else None


def _get_data_quality(data_date):
    if not data_date:
        return {"status_label": "❌ No data", "expected_count": get_active_etf_count(), "actual_count": 0, "failed_etfs": [], "change_skips": [], "warnings": ["⚠️ 無持倉資料"]}
    expected = get_active_etf_count()
    actual = _get_actual_etf_count(data_date)
    failed = _get_failed_etfs(data_date)
    skips = _get_skipped_change_diagnostics(data_date, _get_previous_holdings_date(data_date))
    warnings = _get_data_warnings(data_date)
    degraded = bool(warnings or failed or skips or (expected and actual < expected))
    return {"status_label": "⚠️ Degraded" if degraded else "✅ Clean", "expected_count": expected, "actual_count": actual, "failed_etfs": failed, "change_skips": skips, "warnings": warnings}


def _get_actual_etf_count(data_date):
    return len({row["etf_code"] for row in _canonical_rows(data_date)})


def _get_failed_etfs(data_date):
    try:
        with _using_row_factory(None) as conn:
            rows = conn.execute("SELECT etf_code FROM etf_scrape_runs WHERE date = ? AND status = 'failed' ORDER BY etf_code", (data_date,)).fetchall()
        return [row[0] for row in rows]
    except sqlite3.OperationalError:
        return []


def _get_skipped_change_diagnostics(data_date, prev_date):
    if not data_date or not prev_date:
        return []
    try:
        with _using_row_factory(_dict_factory) as conn:
            return conn.execute(
                """SELECT etf_code, reason, current_source_type, previous_source_type
                   FROM etf_change_diagnostics
                   WHERE date = ? AND prev_date = ? AND status = 'skipped'
                   ORDER BY etf_code""",
                (data_date, prev_date),
            ).fetchall()
    except sqlite3.OperationalError:
        return []


def _get_data_warnings(data_date):
    warnings = []
    expected = get_active_etf_count()
    actual = _get_actual_etf_count(data_date)
    if expected and actual < expected:
        warnings.append(f"⚠️ 資料不完整: 預期 {expected} 檔 ETF，實際取得 {actual} 檔")
    totals = {}
    for row in _canonical_stock_rows(data_date):
        totals[row["etf_code"]] = totals.get(row["etf_code"], 0.0) + (row.get("weight_pct") or 0.0)
    for etf_code, total in sorted(totals.items()):
        if total < 80.0:
            warnings.append(f"⚠️ {etf_code}: 股票權重僅 {total:.1f}%，可能資料不完整")
    failed = _get_failed_etfs(data_date)
    if failed:
        warnings.append(f"⚠️ 抓取失敗: {', '.join(failed)}")
    return warnings


def _get_summary_stats(data_date):
    rows = _canonical_rows(data_date) if data_date else []
    return {"etf_count": len({row["etf_code"] for row in rows}), "stock_count": len({row["stock_code"] for row in rows if row.get("asset_type") == "stock"}), "non_stock_count": sum(1 for row in rows if row.get("asset_type") != "stock")}


def _get_change_summary(data_date):
    try:
        with _using_row_factory(_dict_factory) as conn:
            return conn.execute(
                """SELECT COALESCE(SUM(CASE WHEN is_new_position = 1 THEN 1 ELSE 0 END), 0) as new_count,
                          COALESCE(SUM(CASE WHEN is_removed_position = 1 THEN 1 ELSE 0 END), 0) as removed_count,
                          COALESCE(SUM(CASE WHEN is_new_position = 0 AND is_removed_position = 0 AND weight_delta_1d > 0 THEN 1 ELSE 0 END), 0) as increased_count,
                          COALESCE(SUM(CASE WHEN is_new_position = 0 AND is_removed_position = 0 AND weight_delta_1d < 0 THEN 1 ELSE 0 END), 0) as decreased_count
                   FROM etf_holding_changes WHERE date = ?""",
                (data_date,),
            ).fetchone()
    except sqlite3.OperationalError:
        return None


def _get_top_movers(data_date, limit=10):
    try:
        with _using_row_factory(_dict_factory) as conn:
            return conn.execute(
                """SELECT stock_code, stock_name, etf_code, weight_pct as curr_weight, prev_weight_pct as prev_weight,
                          weight_delta_1d, position_change_type, confidence
                   FROM etf_holding_changes
                   WHERE date = ? AND is_new_position = 0 AND is_removed_position = 0
                   ORDER BY ABS(weight_delta_1d) DESC LIMIT ?""",
                (data_date, limit),
            ).fetchall()
    except sqlite3.OperationalError:
        return []


def _get_new_positions(data_date):
    try:
        with _using_row_factory(_dict_factory) as conn:
            return conn.execute(
                """SELECT etf_code, stock_code, stock_name, weight_pct, shares, rank
                   FROM etf_holding_changes
                   WHERE date = ? AND is_new_position = 1 AND (weight_pct >= ? OR (rank IS NOT NULL AND rank <= ?))
                   ORDER BY weight_pct DESC""",
                (data_date, _MATERIAL_POSITION_WEIGHT, _TOP_RANK_CUTOFF),
            ).fetchall()
    except sqlite3.OperationalError:
        return []


def _get_removed_positions(data_date):
    try:
        with _using_row_factory(_dict_factory) as conn:
            return conn.execute(
                """SELECT etf_code, stock_code, stock_name, prev_weight_pct, prev_shares, prev_rank
                   FROM etf_holding_changes
                   WHERE date = ? AND is_removed_position = 1 AND (prev_weight_pct >= ? OR (prev_rank IS NOT NULL AND prev_rank <= ?))
                   ORDER BY prev_weight_pct DESC""",
                (data_date, _MATERIAL_POSITION_WEIGHT, _TOP_RANK_CUTOFF),
            ).fetchall()
    except sqlite3.OperationalError:
        return []


def _get_hidden_position_counts(data_date):
    try:
        with _using_row_factory(None) as conn:
            new_row = conn.execute("SELECT COUNT(*) FROM etf_holding_changes WHERE date = ? AND is_new_position = 1 AND weight_pct < ? AND (rank IS NULL OR rank > ?)", (data_date, _MATERIAL_POSITION_WEIGHT, _TOP_RANK_CUTOFF)).fetchone()
            removed_row = conn.execute("SELECT COUNT(*) FROM etf_holding_changes WHERE date = ? AND is_removed_position = 1 AND prev_weight_pct < ? AND (prev_rank IS NULL OR prev_rank > ?)", (data_date, _MATERIAL_POSITION_WEIGHT, _TOP_RANK_CUTOFF)).fetchone()
        return {"new": new_row[0] if new_row else 0, "removed": removed_row[0] if removed_row else 0}
    except sqlite3.OperationalError:
        return {"new": 0, "removed": 0}


def _group_positions(rows):
    grouped = {}
    for row in rows:
        grouped.setdefault(row["stock_code"], []).append(row)
    return grouped


def _get_consensus_stocks(data_date, min_etfs=15):
    groups = {}
    for row in _canonical_stock_rows(data_date):
        group = groups.setdefault((row["stock_code"], row.get("stock_name")), {"etfs": set(), "weights": []})
        group["etfs"].add(row["etf_code"])
        group["weights"].append(row.get("weight_pct") or 0.0)
    out = []
    for (code, name), group in groups.items():
        if len(group["etfs"]) >= min_etfs:
            out.append({"stock_code": code, "stock_name": name, "etf_count": len(group["etfs"]), "avg_weight": sum(group["weights"]) / len(group["weights"]), "max_weight": max(group["weights"]), "active_etf_count": get_active_etf_count()})
    return sorted(out, key=lambda row: (-row["etf_count"], -row["avg_weight"], row["stock_code"]))


def _get_stock_weight_change(stock_code, current_date, prev_date):
    current = _canonical_stock_weight_sum(stock_code, current_date)
    previous = _canonical_stock_weight_sum(stock_code, prev_date)
    return None if current is None or previous is None else current - previous


def _canonical_stock_weight_sum(stock_code, data_date):
    rows = [row for row in _canonical_stock_rows(data_date) if row.get("stock_code") == stock_code]
    if not rows:
        return None
    return sum(row.get("weight_pct") or 0.0 for row in rows)


def _get_signals(data_date):
    try:
        with _using_row_factory(_dict_factory) as conn:
            rows = conn.execute("SELECT * FROM etf_manager_signals WHERE date = ?", (data_date,)).fetchall()
        return sorted(rows, key=_signal_sort_key)
    except sqlite3.OperationalError:
        return []


def _signal_summary(signals):
    return {"fresh_consensus": sum(1 for row in signals if _is_consensus(row) and _freshness(row) == "new"), "reversals": sum(1 for row in signals if _freshness(row) == "reversal"), "persistent_consensus": sum(1 for row in signals if _is_consensus(row) and _freshness(row) == "persistent"), "stale_or_fading": sum(1 for row in signals if _freshness(row) in {"stale", "fading"})}


def _signal_sort_key(row):
    return (FRESHNESS_ORDER.get(_freshness(row), 9), -abs(row.get("signal_score") or 0), row.get("stock_code") or "")


def _is_consensus(row):
    return str(row.get("signal_type") or "").startswith("consensus_")


def _freshness(row):
    return row.get("signal_freshness") or "current"


def _format_signal_line(row):
    direction = "REDUCE" if (row.get("signal_score") or 0) < 0 or "reduce" in (row.get("signal_type") or "") else "ADD"
    return f"{direction} {row.get('stock_code')} {row.get('stock_name') or ''} | {row.get('signal_type')} | {_freshness(row)} | score={(row.get('signal_score') or 0):+.0f} | conf={row.get('confidence') or 'normal'}"


def _fmt_pct(value, suffix="%"):
    return "N/A" if value is None else f"{value:.2f}{suffix}"


def _fmt_signed(value):
    return "N/A" if value is None else f"{value:+.2f}"


def generate_daily_report(summary: dict) -> str:
    now = datetime.now(CST)
    lines = [
        "📊 台灣主動 ETF 每日持倉報告",
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
        "**數據量**:",
        f"  股票持倉行數: {summary['total_stock_rows']}",
        f"  非股票資產行數: {summary['total_non_stock_rows']}",
    ]
    return "\n".join(lines)


def get_latest_signal_date():
    try:
        with _using_row_factory(None) as conn:
            row = conn.execute("SELECT MAX(date) FROM etf_manager_signals").fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row and row[0] else None
