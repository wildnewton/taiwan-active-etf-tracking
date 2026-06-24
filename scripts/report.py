"""Daily report generators for Taiwan Active ETF tracking."""
import json
import sqlite3
from datetime import datetime, timezone, timedelta, date

import db

CST = timezone(timedelta(hours=8))


def generate_signal_report(signal_date=None) -> str:
    """Generate a concise, investment-focused daily report.

    Structure:
    1. Executive summary (data status + key numbers)
    2. 🔥 Top movers (biggest weight changes across all ETFs)
    3. 🆕 New positions / ❌ Removed positions
    4. 📊 Consensus view (stocks held by most ETFs)
    5. ⚠️ Warnings (data quality, missing ETFs)
    6. Signal details (if available)
    """
    now = datetime.now(CST)
    today = date.today()
    data_date = _get_latest_holdings_date()
    prev_date = _get_previous_holdings_date(data_date)

    lines = []
    lines.append(f"📊 台灣主動 ETF 每日報告")
    lines.append(f"📅 {now.strftime('%Y-%m-%d %H:%M')} CST | 資料日期: {data_date or 'N/A'}")
    lines.append("")

    # ── 1. Executive Summary ──
    stats = _get_summary_stats(data_date)
    prev_stats = _get_summary_stats(prev_date) if prev_date else None

    lines.append("═══ 摘要 ═══")
    lines.append(f"ETF 數量: {stats['etf_count']} | 股票檔數: {stats['stock_count']} | 非股票資產: {stats['non_stock_count']}")
    
    # Change summary
    changes = _get_change_summary(data_date)
    if changes:
        lines.append(f"較前日 ({prev_date}): 🟢 {changes['new_count']} 新增 | 🔴 {changes['removed_count']} 移除 | 📈 {changes['increased_count']} 增持 | 📉 {changes['decreased_count']} 減持")
    lines.append("")

    # ── 2. Top Movers ──
    top_movers = _get_top_movers(data_date, limit=10)
    if top_movers:
        lines.append("═══ 🔥 最大變動 (全市場) ═══")
        for m in top_movers:
            arrow = "📈" if m["weight_delta_1d"] > 0 else "📉"
            etf_label = f"[{m['etf_code']}]" if m["etf_count"] == 1 else f"[{m['etf_count']}檔]"
            lines.append(
                f"  {arrow} {m['stock_code']} {m['stock_name']:8s} "
                f"{m['prev_weight']:.1f}% → {m['curr_weight']:.1f}% "
                f"(Δ{m['weight_delta_1d']:+.2f}) {etf_label}"
            )
        lines.append("")

    # ── 3. New / Removed positions ──
    new_positions = _get_new_positions(data_date)
    removed_positions = _get_removed_positions(data_date)

    if new_positions:
        lines.append("═══ 🆕 新增部位 ═══")
        # Group by stock, show which ETFs added
        grouped = _group_positions(new_positions)
        for stock_code, entries in grouped.items():
            etf_list = ", ".join(e["etf_code"] for e in entries)
            max_weight = max(e["weight_pct"] for e in entries)
            stock_name = entries[0]["stock_name"]
            lines.append(f"  ➕ {stock_code} {stock_name:8s} 最大 {max_weight:.2f}% ({etf_list})")
        lines.append("")

    if removed_positions:
        lines.append("═══ ❌ 移除部位 ═══")
        grouped = _group_positions(removed_positions, use_prev=True)
        for stock_code, entries in grouped.items():
            etf_list = ", ".join(e["etf_code"] for e in entries)
            max_weight = max(e["prev_weight_pct"] for e in entries)
            stock_name = entries[0]["stock_name"]
            lines.append(f"  ➖ {stock_code} {stock_name:8s} 原 {max_weight:.2f}% ({etf_list})")
        lines.append("")

    # ── 4. Consensus View ──
    consensus = _get_consensus_stocks(data_date, min_etfs=15)
    if consensus:
        lines.append("═══ 📊 高共識持股 (≥15 檔 ETF) ═══")
        for s in consensus:
            delta_str = ""
            if prev_date:
                delta = _get_stock_weight_change(s["stock_code"], data_date, prev_date)
                if delta is not None:
                    delta_str = f" (Δ{delta:+.2f}%)"
            lines.append(
                f"  {s['stock_code']} {s['stock_name']:8s} "
                f"{s['etf_count']}檔 | 總權重 {s['total_weight']:.1f}%{delta_str}"
            )
        lines.append("")

    # ── 5. Investment Observations ──
    observations = _generate_observations(data_date, prev_date, top_movers, new_positions, removed_positions, consensus)
    if observations:
        lines.append("═══ 💡 投資觀察 ═══")
        for obs in observations:
            lines.append(f"  • {obs}")
        lines.append("")

    # ── 6. Warnings ──
    warnings = _get_data_warnings(data_date)
    if warnings:
        lines.append("═══ ⚠️ 資料品質警告 ═══")
        for w in warnings:
            lines.append(f"  {w}")
        lines.append("")

    # ── 7. Signals (if available) ──
    signals = _get_signals(data_date)
    if signals:
        lines.append("═══ 📈 管理人訊號 ═══")
        for sig in signals[:20]:  # Top 20 only
            lines.append(f"  {sig['action_label']}: {sig['stock_code']} {sig['stock_name']} ({sig['signal_type']}, score={sig['signal_score']:+.0f})")
        if len(signals) > 20:
            lines.append(f"  ... 另有 {len(signals) - 20} 個訊號")
        lines.append("")

    return "\n".join(lines)


# ── Data access helpers ──

def _get_latest_holdings_date():
    conn = db._connect()
    row = conn.execute("SELECT MAX(date) FROM etf_daily_holdings").fetchone()
    return row[0] if row and row[0] else None


def _get_previous_holdings_date(current_date):
    if not current_date:
        return None
    conn = db._connect()
    row = conn.execute(
        "SELECT MAX(date) FROM etf_daily_holdings WHERE date < ?", (current_date,)
    ).fetchone()
    return row[0] if row and row[0] else None


def _get_summary_stats(data_date):
    if not data_date:
        return {"etf_count": 0, "stock_count": 0, "non_stock_count": 0}
    conn = db._connect()
    old_factory = conn.row_factory
    conn.row_factory = None
    try:
        row = conn.execute(
            "SELECT COUNT(DISTINCT etf_code) FROM etf_daily_holdings WHERE date = ?",
            (data_date,),
        ).fetchone()
        etf_count = row[0] if row else 0

        row2 = conn.execute(
            "SELECT COUNT(DISTINCT stock_code) FROM etf_daily_holdings WHERE date = ? AND asset_type = 'stock'",
            (data_date,),
        ).fetchone()
        stock_count = row2[0] if row2 else 0

        row3 = conn.execute(
            "SELECT COUNT(*) FROM etf_daily_holdings WHERE date = ? AND asset_type != 'stock'",
            (data_date,),
        ).fetchone()
        non_stock_count = row3[0] if row3 else 0
    finally:
        conn.row_factory = old_factory

    return {"etf_count": etf_count, "stock_count": stock_count, "non_stock_count": non_stock_count}


def _get_change_summary(data_date):
    if not data_date:
        return None
    conn = db._connect()
    conn.row_factory = _dict_factory
    try:
        row = conn.execute(
            """SELECT 
                SUM(CASE WHEN is_new_position = 1 THEN 1 ELSE 0 END) as new_count,
                SUM(CASE WHEN is_removed_position = 1 THEN 1 ELSE 0 END) as removed_count,
                SUM(CASE WHEN is_new_position = 0 AND is_removed_position = 0 AND weight_delta_1d > 0 THEN 1 ELSE 0 END) as increased_count,
                SUM(CASE WHEN is_new_position = 0 AND is_removed_position = 0 AND weight_delta_1d < 0 THEN 1 ELSE 0 END) as decreased_count
            FROM etf_holding_changes WHERE date = ?""",
            (data_date,),
        ).fetchone()
        return row if row else None
    except sqlite3.OperationalError:
        return None


def _get_top_movers(data_date, limit=10):
    if not data_date:
        return []
    conn = db._connect()
    conn.row_factory = _dict_factory
    try:
        rows = conn.execute(
            """SELECT stock_code, stock_name, etf_code,
                      weight_pct as curr_weight, prev_weight_pct as prev_weight,
                      weight_delta_1d, shares_delta_1d,
                      1 as etf_count
               FROM etf_holding_changes
               WHERE date = ?
                 AND is_new_position = 0
                 AND is_removed_position = 0
               ORDER BY ABS(weight_delta_1d) DESC
               LIMIT ?""",
            (data_date, limit),
        ).fetchall()
        return rows
    except sqlite3.OperationalError:
        return []


def _get_new_positions(data_date):
    if not data_date:
        return []
    conn = db._connect()
    conn.row_factory = _dict_factory
    try:
        return conn.execute(
            """SELECT etf_code, stock_code, stock_name, weight_pct, shares
               FROM etf_holding_changes
               WHERE date = ? AND is_new_position = 1
               ORDER BY weight_pct DESC""",
            (data_date,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []


def _get_removed_positions(data_date):
    if not data_date:
        return []
    conn = db._connect()
    conn.row_factory = _dict_factory
    try:
        return conn.execute(
            """SELECT etf_code, stock_code, stock_name, prev_weight_pct, prev_shares
               FROM etf_holding_changes
               WHERE date = ? AND is_removed_position = 1
               ORDER BY prev_weight_pct DESC""",
            (data_date,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []


def _group_positions(positions, use_prev=False):
    """Group positions by stock_code."""
    grouped = {}
    weight_key = "prev_weight_pct" if use_prev else "weight_pct"
    for p in positions:
        code = p["stock_code"]
        if code not in grouped:
            grouped[code] = []
        grouped[code].append(p)
    return grouped


def _get_consensus_stocks(data_date, min_etfs=15):
    if not data_date:
        return []
    conn = db._connect()
    conn.row_factory = _dict_factory
    try:
        return conn.execute(
            """SELECT stock_code, stock_name,
                      COUNT(DISTINCT etf_code) as etf_count,
                      SUM(weight_pct) as total_weight
               FROM etf_daily_holdings
               WHERE date = ? AND asset_type = 'stock'
               GROUP BY stock_code, stock_name
               HAVING etf_count >= ?
               ORDER BY etf_count DESC, total_weight DESC""",
            (data_date, min_etfs),
        ).fetchall()
    except sqlite3.OperationalError:
        return []


def _get_stock_weight_change(stock_code, current_date, prev_date):
    """Get the total weight change for a stock across all ETFs."""
    conn = db._connect()
    old_factory = conn.row_factory
    conn.row_factory = None
    try:
        curr = conn.execute(
            "SELECT SUM(weight_pct) FROM etf_daily_holdings WHERE date = ? AND stock_code = ? AND asset_type = 'stock'",
            (current_date, stock_code),
        ).fetchone()
        prev = conn.execute(
            "SELECT SUM(weight_pct) FROM etf_daily_holdings WHERE date = ? AND stock_code = ? AND asset_type = 'stock'",
            (prev_date, stock_code),
        ).fetchone()
    finally:
        conn.row_factory = old_factory
    if curr and curr[0] and prev and prev[0]:
        return curr[0] - prev[0]
    return None


def _generate_observations(data_date, prev_date, top_movers, new_positions, removed_positions, consensus):
    """Generate investment-relevant observations."""
    observations = []

    if not data_date or not prev_date:
        return observations

    # Observation: stocks with biggest consensus shifts
    if consensus:
        gaining_consensus = []
        losing_consensus = []
        for s in consensus:
            delta = _get_stock_weight_change(s["stock_code"], data_date, prev_date)
            if delta and abs(delta) > 1.0:
                if delta > 0:
                    gaining_consensus.append((s, delta))
                else:
                    losing_consensus.append((s, delta))

        if gaining_consensus:
            gaining_consensus.sort(key=lambda x: x[1], reverse=True)
            names = ", ".join(f"{s['stock_code']} {s['stock_name']}(+{d:.1f}%)" for s, d in gaining_consensus[:3])
            observations.append(f"高共識增持: {names}")

        if losing_consensus:
            losing_consensus.sort(key=lambda x: x[1])
            names = ", ".join(f"{s['stock_code']} {s['stock_name']}({d:.1f}%)" for s, d in losing_consensus[:3])
            observations.append(f"高共識減持: {names}")

    # Observation: ETFs with most active changes
    etf_activity = {}
    for m in top_movers:
        code = m["etf_code"]
        etf_activity[code] = etf_activity.get(code, 0) + 1
    if etf_activity:
        most_active = sorted(etf_activity.items(), key=lambda x: x[1], reverse=True)[:3]
        if most_active[0][1] >= 3:
            names = ", ".join(f"{code}({cnt}檔)" for code, cnt in most_active)
            observations.append(f"最活躍 ETF: {names}")

    # Observation: sector themes (if multiple stocks in same sector move together)
    # TODO: add sector classification

    return observations


def _get_data_warnings(data_date):
    if not data_date:
        return ["⚠️ 無持倉資料"]
    warnings = []
    conn = db._connect()
    old_factory = conn.row_factory
    conn.row_factory = None  # Reset to default tuple factory
    try:
        # Check for low-weight ETFs
        rows = conn.execute(
            """SELECT etf_code, SUM(weight_pct) as total_weight
               FROM etf_daily_holdings
               WHERE date = ? AND asset_type = 'stock'
               GROUP BY etf_code
               HAVING total_weight < 80""",
            (data_date,),
        ).fetchall()
        for r in rows:
            warnings.append(f"⚠️ {r[0]}: 股票權重僅 {r[1]:.1f}%，可能資料不完整")

        # Check for failed scrapes
        try:
            failed = conn.execute(
                "SELECT etf_code FROM etf_scrape_runs WHERE date = ? AND status = 'failed'",
                (data_date,),
            ).fetchall()
            if failed:
                codes = ", ".join(r[0] for r in failed)
                warnings.append(f"⚠️ 抓取失敗: {codes}")
        except sqlite3.OperationalError:
            pass
    finally:
        conn.row_factory = old_factory

    return warnings


def _get_signals(data_date):
    if not data_date:
        return []
    conn = db._connect()
    conn.row_factory = _dict_factory
    try:
        return conn.execute(
            """SELECT * FROM etf_manager_signals
               WHERE date = ?
               ORDER BY ABS(signal_score) DESC""",
            (data_date,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []


def _dict_factory(cursor, row):
    return {column[0]: row[index] for index, column in enumerate(cursor.description)}


# ── Legacy report (kept for backward compatibility) ──

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
    """Generate a human-readable daily report from pipeline summary."""
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
        "**數據量**:",
        f"  股票持倉行數: {summary['total_stock_rows']}",
        f"  非股票資產行數: {summary['total_non_stock_rows']}",
    ]
    return "\n".join(lines)


def get_latest_signal_date():
    """Return the latest date with manager signals, or None if unavailable."""
    conn = db._connect()
    try:
        row = conn.execute("SELECT MAX(date) FROM etf_manager_signals").fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row and row[0] else None
