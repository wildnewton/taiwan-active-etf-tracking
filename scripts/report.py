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
_CORE_POSITION_WEIGHT = 2.0
_TOP_RANK_CUTOFF = 20
_MIN_NEW_POSITION_ETF_COUNT = 2
_MIN_EXPOSURE_ACTIVE_DELTA_PCT = 10.0
_SIGNIFICANT_SIGNAL_SCORE = 6.0
_MANAGER_INTENT_WINDOW = 5
_MANAGER_INTENT_LIMIT = 8
_EXCLUDED_MANAGER_INTENT_STATES = {"neutral", "insufficient_data"}


FRESHNESS_ORDER = {
    "new": 0,
    "reversal": 1,
    "persistent": 2,
    "current": 3,
    "fading": 4,
    "stale": 5,
}


FRESHNESS_LABELS = {
    "new": "new",
    "reversal": "reversal",
    "persistent": "persistent",
    "current": "current",
    "fading": "fading",
    "stale": "stale",
}


_MANAGER_INTENT_PRIORITY = {
    "cross_fund_rotation_accumulation": 0,
    "cross_fund_rotation_distribution": 1,
    "cross_fund_rotation": 2,
    "accumulation": 3,
    "distribution": 4,
    "contested": 5,
    "high_activity_unclear": 6,
}


def generate_signal_report(signal_date=None, quality_run_date=None) -> str:
    """Generate a concise, decision-grade daily report.

    The report intentionally separates data trust, manager-action signals,
    manager-intent rollups, exposure movement, and low-materiality noise so users
    do not read every weight change as an active manager trade.
    """
    now = datetime.now(CST)
    data_date = signal_date or _get_latest_holdings_date()
    quality_run_date = quality_run_date or (
        signal_date if signal_date is not None else _get_latest_scrape_run_date()
    )
    prev_date = _get_previous_holdings_date(data_date)

    lines = []
    lines.append("📊 台灣主動 ETF 每日報告")
    lines.append(f"📅 {now.strftime('%Y-%m-%d %H:%M')} CST | 資料日期: {data_date or 'N/A'}")
    lines.append("")

    quality = _get_data_quality(data_date, quality_run_date=quality_run_date)
    lines.extend(_render_data_quality(quality))

    stats = _get_summary_stats(data_date)
    changes = _get_change_summary(data_date)
    signals = _get_signals(data_date)

    lines.append("═══ 摘要 ═══")
    lines.append(f"ETF 數量: {stats['etf_count']} | 股票檔數: {stats['stock_count']} | 非股票資產: {stats['non_stock_count']}")
    if changes:
        lines.append(
            f"較前日 ({prev_date or 'N/A'}): "
            f"🟢 {changes['new_count']} 新增 | "
            f"🔴 {changes['removed_count']} 移除 | "
            f"📊 {changes['increased_count']} 權重上升 | "
            f"📊 {changes['decreased_count']} 權重下降"
        )
    if signals:
        signal_stats = _signal_summary(signals)
        lines.append(
            "今日訊號: "
            f"🔥 fresh {signal_stats['fresh_consensus']} | "
            f"🔁 reversal {signal_stats['reversals']} | "
            f"📈 persistent {signal_stats['persistent_consensus']} | "
            f"🧊 stale/fading {signal_stats['stale_or_fading']}"
        )
    lines.append("")

    if signals:
        lines.extend(_render_manager_signals(signals))

    manager_intents = _get_manager_intent_rollups(data_date)
    if manager_intents:
        lines.extend(_render_manager_intent_radar(manager_intents))

    top_movers = _get_top_movers(data_date, limit=10)
    lines.extend(_render_exposure_movers(top_movers))

    new_positions = _get_new_positions(data_date)
    removed_positions = _get_removed_positions(data_date)
    hidden_counts = _get_hidden_position_counts(data_date)
    lines.extend(_render_new_removed_positions(new_positions, removed_positions, hidden_counts))

    consensus = _get_consensus_stocks(data_date, min_etfs=15)
    lines.extend(_render_consensus_holdings(consensus, data_date, prev_date))

    observations = _generate_observations(data_date, prev_date, top_movers, new_positions, removed_positions, consensus)
    if observations:
        lines.append("═══ 💡 投資觀察 ═══")
        for obs in observations:
            lines.append(f"  • {obs}")
        lines.append("")

    return "\n".join(lines).rstrip()


# ── Render helpers ──

def _render_data_quality(quality: dict) -> list[str]:
    lines = ["═══ 資料品質 / 信任度 ═══"]
    lines.append(f"資料品質: {quality['status_label']}")
    if quality.get("quality_run_date"):
        lines.append(f"抓取執行日: {quality['quality_run_date']}")
    freshness = quality.get("scrape_freshness") or _empty_scrape_freshness()
    stale_rows = freshness.get("stale") or []
    unknown_rows = freshness.get("unknown") or []
    fresh_rows = freshness.get("fresh") or []
    if quality["expected_count"]:
        lines.append(f"Active ETF universe: {quality['expected_count']} | 成功持倉 ETF: {quality['actual_count']}/{quality['expected_count']}")
    else:
        lines.append(f"成功持倉 ETF: {quality['actual_count']}")
    if fresh_rows or stale_rows or unknown_rows:
        denominator = quality["expected_count"] or len(fresh_rows) + len(stale_rows) + len(unknown_rows)
        lines.append(
            f"資料新鮮度: fresh {len(fresh_rows)}/{denominator} | "
            f"stale {len(stale_rows)} | unknown {len(unknown_rows)}"
        )
    if stale_rows or unknown_rows:
        lines.append("報告狀態: ⚠️ Provisional / 暫定（部分 ETF 尚非當日可確認資料；避免全體化結論）")
    if stale_rows:
        lines.append("資料日期落後:")
        for row in stale_rows:
            lines.append(f"  {row['etf_code']} source {row.get('data_date') or 'N/A'}")
    if unknown_rows:
        lines.append("資料日期未知:")
        for row in unknown_rows:
            source_date = row.get("data_date") or "unknown"
            lines.append(f"  {row['etf_code']} source {source_date}")
    if quality["failed_etfs"]:
        lines.append(f"抓取失敗: {', '.join(quality['failed_etfs'])}")
    if quality.get("change_skips"):
        lines.append("變更偵測跳過:")
        for row in quality["change_skips"]:
            current_source = row.get("current_source_type") or "None"
            previous_source = row.get("previous_source_type") or "None"
            lines.append(
                f"  {row['etf_code']} {row.get('reason') or 'unknown'} "
                f"({current_source}→{previous_source})"
            )
    if quality["warnings"]:
        lines.append("資料品質警告:")
        for warning in quality["warnings"]:
            lines.append(f"  {warning}")
    else:
        lines.append("資料品質警告: 無")
    lines.append("")
    return lines


def _render_manager_signals(signals: list[dict]) -> list[str]:
    signals = [row for row in signals if _is_significant_signal(row)]
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
        rows = sorted(rows, key=_signal_sort_key)[:8]
        lines.append(title)
        for row in rows:
            emitted.add(row.get("signal_id"))
            lines.append(f"  {_format_signal_line(row)}")
        row_ids = {row.get("signal_id") for row in rows}
        remaining = len([row for row in signals if predicate(row) and row.get("signal_id") not in row_ids])
        if remaining > 0:
            lines.append(f"  ... 另有 {remaining} 個同類訊號")
    if not emitted:
        lines.append("  無可排序的管理人訊號")
    lines.append("")
    return lines


def _render_manager_intent_radar(rows: list[dict]) -> list[str]:
    lines = ["═══ 🧠 Manager Intent Radar ═══"]
    sections = [
        ("🔥 Accumulation patterns", lambda row: row.get("primary_intent_state") in {"accumulation", "cross_fund_rotation_accumulation"}),
        ("❄️ Distribution patterns", lambda row: row.get("primary_intent_state") in {"distribution", "cross_fund_rotation_distribution"}),
        ("🔄 Cross-fund rotation / unclear", lambda row: row.get("primary_intent_state") == "cross_fund_rotation"),
        ("⚡ Contested / unclear", lambda row: row.get("primary_intent_state") in {"contested", "high_activity_unclear"}),
    ]
    emitted = set()
    for title, predicate in sections:
        section_rows = [row for row in rows if predicate(row)]
        if not section_rows:
            continue
        lines.append(title)
        for row in section_rows[:4]:
            emitted.add(_manager_intent_key(row))
            lines.append(f"  {_format_manager_intent_line(row)}")
    remaining = len([row for row in rows if _manager_intent_key(row) not in emitted])
    if remaining > 0:
        lines.append(f"  ... 另有 {remaining} 個管理人意圖候選")
    lines.append("")
    return lines


def _format_manager_intent_line(row: dict) -> str:
    entity = "stock-level"
    if row.get("entity_level") == "issuer_stock":
        entity = f"issuer: {row.get('issuer') or row.get('issuer_key') or 'unknown'}"
    label = _manager_intent_label(row)
    net = row.get("net_active_score") or 0.0
    gross = row.get("gross_active_score") or 0.0
    offset = row.get("cross_fund_offset_ratio")
    offset_text = f" | offset {offset:.0%}" if offset is not None else ""
    day_text = f" | buy/sell days {row.get('buy_days') or 0}/{row.get('sell_days') or 0}"
    interpretation = _manager_intent_interpretation(row)
    return (
        f"{row.get('stock_code')} {row.get('stock_name') or ''} | {entity} | {label} | "
        f"net {net:+.1f} / gross {gross:.1f}{offset_text}{day_text} | "
        f"pattern consistent with {interpretation} | conf={row.get('confidence') or 'low'}"
    )


def _manager_intent_label(row: dict) -> str:
    state = row.get("primary_intent_state") or "unknown"
    labels = {
        "accumulation": "accumulation",
        "distribution": "distribution",
        "cross_fund_rotation_accumulation": "cross-fund rotation accumulation",
        "cross_fund_rotation_distribution": "cross-fund rotation distribution",
        "cross_fund_rotation": "cross-fund rotation / unclear",
        "contested": "issuer disagreement / contested",
        "high_activity_unclear": "high activity unclear",
    }
    return labels.get(state, state.replace("_", " "))


def _manager_intent_interpretation(row: dict) -> str:
    state = row.get("primary_intent_state") or ""
    if state == "cross_fund_rotation_accumulation":
        return "same-issuer fund-level rotation while net exposure increased"
    if state == "cross_fund_rotation_distribution":
        return "same-issuer fund-level rotation while net exposure decreased"
    if state == "cross_fund_rotation":
        return "same-issuer fund-level rotation; net direction unclear"
    if state == "accumulation":
        return "broad active accumulation"
    if state == "distribution":
        return "broad active distribution"
    if state == "contested":
        return "different issuers disagreeing"
    if state == "high_activity_unclear":
        return "high gross activity with low directional clarity"
    return state.replace("_", " ")


def _render_exposure_movers(top_movers: list[dict]) -> list[str]:
    lines = ["═══ 📊 Exposure movers（權重變動，不等於經理人交易） ═══"]
    if not top_movers:
        lines.append("  無重大權重變動資料")
        lines.append("")
        return lines
    for mover in top_movers:
        arrow = "📈" if (mover.get("weight_delta_1d") or 0) > 0 else "📉"
        classification = mover.get("position_change_type") or "unknown"
        active_delta = _fmt_pct(mover.get("active_shares_delta_pct_1d"), suffix="% activeΔ")
        confidence = mover.get("confidence") or "normal"
        lines.append(
            f"  {arrow} {mover['stock_code']} {mover.get('stock_name') or ''} "
            f"[{mover['etf_code']}] "
            f"{_fmt_pct(mover.get('prev_weight'))} → {_fmt_pct(mover.get('curr_weight'))} "
            f"(權重Δ{_fmt_signed(mover.get('weight_delta_1d'))}) | "
            f"{classification} | {active_delta} | conf={confidence}"
        )
    lines.append("")
    return lines


def _render_new_removed_positions(new_positions: list[dict], removed_positions: list[dict], hidden_counts: dict) -> list[str]:
    lines = []
    if new_positions:
        lines.append("═══ 🆕 重要新增部位 ═══")
        grouped = _group_positions(new_positions)
        for stock_code, entries in grouped.items():
            etf_list = ", ".join(e["etf_code"] for e in entries)
            etf_count = len({e["etf_code"] for e in entries})
            max_weight = max(e["weight_pct"] for e in entries)
            total_weight = sum(e["weight_pct"] or 0.0 for e in entries)
            stock_name = entries[0].get("stock_name") or ""
            lines.append(
                f"  ➕ {stock_code} {stock_name} 總權重 {total_weight:.2f}% | "
                f"最大 {max_weight:.2f}% | {etf_count}檔ETF ({etf_list})"
            )
        if hidden_counts["new"]:
            lines.append(f"  低權重新增已隱藏: {hidden_counts['new']}")
        lines.append("")
    elif hidden_counts["new"]:
        lines.append("═══ 🆕 重要新增部位 ═══")
        lines.append(f"  無重要新增；低權重新增已隱藏: {hidden_counts['new']}")
        lines.append("")

    if removed_positions:
        lines.append("═══ ❌ 重要移除部位 ═══")
        grouped = _group_positions(removed_positions)
        for stock_code, entries in grouped.items():
            etf_list = ", ".join(e["etf_code"] for e in entries)
            max_weight = max(e["prev_weight_pct"] for e in entries)
            stock_name = entries[0].get("stock_name") or ""
            lines.append(f"  ➖ {stock_code} {stock_name} 原 {max_weight:.2f}% ({etf_list})")
        if hidden_counts["removed"]:
            lines.append(f"  低權重移除已隱藏: {hidden_counts['removed']}")
        lines.append("")
    elif hidden_counts["removed"]:
        lines.append("═══ ❌ 重要移除部位 ═══")
        lines.append(f"  無重要移除；低權重移除已隱藏: {hidden_counts['removed']}")
        lines.append("")
    return lines


def _render_consensus_holdings(consensus: list[dict], data_date, prev_date) -> list[str]:
    if not consensus:
        return []
    lines = ["═══ 📊 高共識持股快照（不是買賣訊號） ═══"]
    for stock in consensus:
        delta = _get_stock_weight_change(stock["stock_code"], data_date, prev_date) if prev_date else None
        delta_str = f" | 總權重Δ{delta:+.2f}%" if delta is not None else ""
        lines.append(
            f"  {stock['stock_code']} {stock.get('stock_name') or ''} "
            f"{stock['etf_count']}/{stock.get('active_etf_count') or '?'}檔 | "
            f"平均 {stock['avg_weight']:.2f}% | 最高 {stock['max_weight']:.2f}%{delta_str}"
        )
    lines.append("")
    return lines


# ── Data access helpers ──

@contextmanager
def _using_row_factory(factory):
    conn = db._connect()
    old_factory = conn.row_factory
    conn.row_factory = factory
    try:
        yield conn
    finally:
        conn.row_factory = old_factory


def _canonical_rows(data_date: str) -> list[dict]:
    if not data_date:
        return []
    canonical_sources = _select_canonical_sources(data_date)
    if not canonical_sources:
        return []
    try:
        with _using_row_factory(_dict_factory) as conn:
            rows = conn.execute(
                """SELECT date, etf_code, asset_name, asset_type, stock_code, stock_name,
                          shares, weight_pct, source_url, source_type, extraction_method, scraped_at
                   FROM etf_daily_holdings
                   WHERE date = ?""",
                (data_date,),
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        row
        for row in rows
        if row.get("source_type") == canonical_sources.get(row.get("etf_code"), {}).get("source_type")
    ]


def _canonical_stock_rows(data_date: str) -> list[dict]:
    return [row for row in _canonical_rows(data_date) if row.get("asset_type") == "stock"]


def _get_latest_holdings_date():
    with _using_row_factory(None) as conn:
        row = conn.execute("SELECT MAX(date) FROM etf_daily_holdings").fetchone()
    return row[0] if row and row[0] else None


def _get_latest_scrape_run_date():
    try:
        with _using_row_factory(None) as conn:
            row = conn.execute("SELECT MAX(date) FROM etf_scrape_runs").fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row and row[0] else None


def _get_previous_holdings_date(current_date):
    if not current_date:
        return None
    with _using_row_factory(None) as conn:
        row = conn.execute("SELECT MAX(date) FROM etf_daily_holdings WHERE date < ?", (current_date,)).fetchone()
    return row[0] if row and row[0] else None


def _empty_scrape_freshness():
    return {"fresh": [], "stale": [], "unknown": []}


def _get_data_quality(data_date, quality_run_date=None):
    quality_run_date = quality_run_date or data_date or _get_latest_scrape_run_date()
    if not data_date:
        return {
            "status_label": "❌ No data",
            "quality_run_date": quality_run_date,
            "expected_count": get_active_etf_count(),
            "actual_count": 0,
            "failed_etfs": [],
            "change_skips": [],
            "scrape_freshness": _empty_scrape_freshness(),
            "warnings": ["⚠️ 無持倉資料"],
        }
    expected_count = get_active_etf_count(as_of_date=data_date)
    actual_count = _get_actual_etf_count(data_date)
    failed_etfs = _get_failed_etfs(quality_run_date)
    change_skips = _get_skipped_change_diagnostics(data_date)
    scrape_freshness = _get_scrape_data_freshness(quality_run_date)
    warnings = _get_data_warnings(data_date)
    freshness_issues = bool(scrape_freshness["stale"] or scrape_freshness["unknown"])
    degraded = bool(warnings or failed_etfs or change_skips or freshness_issues or (expected_count and actual_count < expected_count))
    return {
        "status_label": "⚠️ Degraded" if degraded else "✅ Clean",
        "quality_run_date": quality_run_date,
        "expected_count": expected_count,
        "actual_count": actual_count,
        "failed_etfs": failed_etfs,
        "change_skips": change_skips,
        "scrape_freshness": scrape_freshness,
        "warnings": warnings,
    }


def _get_actual_etf_count(data_date):
    return len({row["etf_code"] for row in _canonical_rows(data_date)})


def _get_failed_etfs(run_date):
    if not run_date:
        return []
    try:
        with _using_row_factory(None) as conn:
            rows = conn.execute(
                """SELECT sr.etf_code
                   FROM etf_scrape_runs sr
                   JOIN etf_universe u ON sr.etf_code = u.code
                   WHERE sr.date = ? AND sr.status = 'failed'
                     AND u.retired = 0
                      AND (u.listing_date IS NULL OR u.listing_date <= sr.date)
                   ORDER BY sr.etf_code""",
                (run_date,),
            ).fetchall()
        return [row[0] for row in rows]
    except sqlite3.OperationalError:
        return []


def _get_scrape_data_freshness(run_date):
    freshness = _empty_scrape_freshness()
    if not run_date:
        return freshness
    try:
        with _using_row_factory(_dict_factory) as conn:
            rows = conn.execute(
                """SELECT sr.etf_code, sr.data_date, sr.status
                   FROM etf_scrape_runs sr
                   JOIN etf_universe u ON sr.etf_code = u.code
                   WHERE sr.date = ?
                     AND sr.status IN ('success', 'stale')
                     AND u.retired = 0
                      AND (u.listing_date IS NULL OR u.listing_date <= sr.date)
                   ORDER BY sr.etf_code""",
                (run_date,),
            ).fetchall()
    except sqlite3.OperationalError:
        return freshness

    for row in rows:
        source_date = row.get("data_date")
        status = row.get("status")
        item = {"etf_code": row.get("etf_code"), "data_date": source_date}
        if not source_date:
            freshness["unknown"].append(item)
        elif status == "stale":
            freshness["stale"].append(item)
        elif status == "success":
            freshness["fresh"].append(item)
        else:
            freshness["unknown"].append(item)
    return freshness


def _get_skipped_change_diagnostics(data_date):
    if not data_date:
        return []
    try:
        with _using_row_factory(_dict_factory) as conn:
            return conn.execute(
                """SELECT d.etf_code, d.reason, d.current_source_type, d.previous_source_type
                   FROM etf_change_diagnostics d
                   JOIN etf_universe u ON d.etf_code = u.code
                   WHERE d.date = ?
                     AND d.status = 'skipped'
                     AND u.retired = 0
                      AND (u.listing_date IS NULL OR u.listing_date <= d.date)
                     AND d.created_at = (
                         SELECT MAX(created_at)
                         FROM etf_change_diagnostics
                         WHERE date = ?
                     )
                   ORDER BY d.etf_code""",
                (data_date, data_date),
            ).fetchall()
    except sqlite3.OperationalError:
        return []


def _get_summary_stats(data_date):
    if not data_date:
        return {"etf_count": 0, "stock_count": 0, "non_stock_count": 0}
    rows = _canonical_rows(data_date)
    return {
        "etf_count": len({row["etf_code"] for row in rows}),
        "stock_count": len({row["stock_code"] for row in rows if row.get("asset_type") == "stock"}),
        "non_stock_count": sum(1 for row in rows if row.get("asset_type") != "stock"),
    }


def _get_change_summary(data_date):
    if not data_date:
        return None
    try:
        with _using_row_factory(_dict_factory) as conn:
            row = conn.execute(
                """SELECT
                    COALESCE(SUM(CASE WHEN is_new_position = 1 THEN 1 ELSE 0 END), 0) as new_count,
                    COALESCE(SUM(CASE WHEN is_removed_position = 1 THEN 1 ELSE 0 END), 0) as removed_count,
                    COALESCE(SUM(CASE WHEN is_new_position = 0 AND is_removed_position = 0 AND weight_delta_1d > 0 THEN 1 ELSE 0 END), 0) as increased_count,
                    COALESCE(SUM(CASE WHEN is_new_position = 0 AND is_removed_position = 0 AND weight_delta_1d < 0 THEN 1 ELSE 0 END), 0) as decreased_count
                FROM etf_holding_changes WHERE date = ?""",
                (data_date,),
            ).fetchone()
        return row if row else None
    except sqlite3.OperationalError:
        return None


def _get_manager_intent_rollups(data_date, limit=_MANAGER_INTENT_LIMIT):
    if not data_date:
        return []
    try:
        with _using_row_factory(_dict_factory) as conn:
            rows = conn.execute(
                """SELECT *
                   FROM manager_intent_rollups
                   WHERE date = ?
                     AND window_days = ?
                     AND primary_intent_state NOT IN ('neutral', 'insufficient_data')""",
                (data_date, _MANAGER_INTENT_WINDOW),
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return sorted(rows, key=_manager_intent_sort_key)[:limit]


def _manager_intent_sort_key(row):
    return (
        _MANAGER_INTENT_PRIORITY.get(row.get("primary_intent_state"), 99),
        -abs(row.get("net_active_score") or 0),
        -(row.get("gross_active_score") or 0),
        row.get("stock_code") or "",
        row.get("issuer_key") or "",
    )


def _manager_intent_key(row):
    return (
        row.get("window_days"),
        row.get("entity_level"),
        row.get("stock_code"),
        row.get("issuer_key") or "",
    )


def _get_top_movers(data_date, limit=10):
    if not data_date:
        return []
    try:
        with _using_row_factory(_dict_factory) as conn:
            rows = conn.execute(
                """SELECT stock_code, stock_name, etf_code,
                          weight_pct as curr_weight, prev_weight_pct as prev_weight,
                          weight_delta_1d, shares_delta_1d,
                          active_shares_delta_pct_1d, position_change_type,
                          active_direction, confidence
                   FROM etf_holding_changes
                   WHERE date = ?
                     AND is_new_position = 0
                     AND is_removed_position = 0
                     AND position_change_type NOT IN ('passive_weight_increase', 'passive_weight_decrease')
                     AND active_shares_delta_pct_1d IS NOT NULL
                     AND ABS(active_shares_delta_pct_1d) >= ?
                   ORDER BY ABS(weight_delta_1d) DESC
                   LIMIT ?""",
                (data_date, _MIN_EXPOSURE_ACTIVE_DELTA_PCT, limit),
            ).fetchall()
        return rows
    except sqlite3.OperationalError:
        return []


def _get_new_positions(data_date):
    if not data_date:
        return []
    try:
        with _using_row_factory(_dict_factory) as conn:
            return conn.execute(
                """WITH material_new AS (
                       SELECT etf_code, stock_code, stock_name, weight_pct, shares, rank
                       FROM etf_holding_changes
                       WHERE date = ?
                         AND is_new_position = 1
                         AND (weight_pct >= ? OR (rank IS NOT NULL AND rank <= ?))
                   )
                   SELECT etf_code, stock_code, stock_name, weight_pct, shares, rank
                   FROM material_new
                   WHERE stock_code IN (
                       SELECT stock_code
                       FROM material_new
                       GROUP BY stock_code
                       HAVING COUNT(DISTINCT etf_code) >= ?
                   )
                   ORDER BY stock_code, weight_pct DESC""",
                (data_date, _MATERIAL_POSITION_WEIGHT, _TOP_RANK_CUTOFF, _MIN_NEW_POSITION_ETF_COUNT),
            ).fetchall()
    except sqlite3.OperationalError:
        return []


def _get_removed_positions(data_date):
    if not data_date:
        return []
    try:
        with _using_row_factory(_dict_factory) as conn:
            return conn.execute(
                """SELECT etf_code, stock_code, stock_name, prev_weight_pct, prev_shares, prev_rank
                   FROM etf_holding_changes
                   WHERE date = ?
                     AND is_removed_position = 1
                     AND (prev_weight_pct >= ? OR (prev_rank IS NOT NULL AND prev_rank <= ?))
                   ORDER BY prev_weight_pct DESC""",
                (data_date, _MATERIAL_POSITION_WEIGHT, _TOP_RANK_CUTOFF),
            ).fetchall()
    except sqlite3.OperationalError:
        return []


def _get_hidden_position_counts(data_date):
    if not data_date:
        return {"new": 0, "removed": 0}
    try:
        with _using_row_factory(None) as conn:
            new_row = conn.execute(
                """SELECT COUNT(*) FROM etf_holding_changes
                   WHERE date = ? AND is_new_position = 1
                     AND weight_pct < ?
                     AND (rank IS NULL OR rank > ?)""",
                (data_date, _MATERIAL_POSITION_WEIGHT, _TOP_RANK_CUTOFF),
            ).fetchone()
            removed_row = conn.execute(
                """SELECT COUNT(*) FROM etf_holding_changes
                   WHERE date = ? AND is_removed_position = 1
                     AND prev_weight_pct < ?
                     AND (prev_rank IS NULL OR prev_rank > ?)""",
                (data_date, _MATERIAL_POSITION_WEIGHT, _TOP_RANK_CUTOFF),
            ).fetchone()
        return {
            "new": new_row[0] if new_row else 0,
            "removed": removed_row[0] if removed_row else 0,
        }
    except sqlite3.OperationalError:
        return {"new": 0, "removed": 0}


def _group_positions(positions):
    grouped = {}
    for position in positions:
        code = position["stock_code"]
        grouped.setdefault(code, []).append(position)
    return grouped


def _get_consensus_stocks(data_date, min_etfs=15):
    if not data_date:
        return []
    stock_groups = {}
    for row in _canonical_stock_rows(data_date):
        key = (row["stock_code"], row.get("stock_name"))
        group = stock_groups.setdefault(
            key,
            {
                "stock_code": row["stock_code"],
                "stock_name": row.get("stock_name"),
                "etfs": set(),
                "weights": [],
            },
        )
        group["etfs"].add(row["etf_code"])
        group["weights"].append(row["weight_pct"] or 0.0)

    active_count = get_active_etf_count(as_of_date=data_date)
    rows = []
    for group in stock_groups.values():
        etf_count = len(group["etfs"])
        if etf_count < min_etfs:
            continue
        weights = group["weights"]
        rows.append(
            {
                "stock_code": group["stock_code"],
                "stock_name": group["stock_name"],
                "etf_count": etf_count,
                "avg_weight": sum(weights) / len(weights) if weights else 0.0,
                "max_weight": max(weights) if weights else 0.0,
                "total_weight": sum(weights),
                "active_etf_count": active_count,
            }
        )
    return sorted(rows, key=lambda row: (-row["etf_count"], -row["avg_weight"], row["stock_code"]))


def _get_stock_weight_change(stock_code, current_date, prev_date):
    if not stock_code or not current_date or not prev_date:
        return None
    current_weight = _canonical_stock_weight_sum(stock_code, current_date)
    previous_weight = _canonical_stock_weight_sum(stock_code, prev_date)
    if current_weight is not None and previous_weight is not None:
        return current_weight - previous_weight
    return None


def _canonical_stock_weight_sum(stock_code, data_date):
    rows = [row for row in _canonical_stock_rows(data_date) if row.get("stock_code") == stock_code]
    if not rows:
        return None
    return sum(row.get("weight_pct") or 0.0 for row in rows)


def _generate_observations(data_date, prev_date, top_movers, new_positions, removed_positions, consensus):
    observations = []
    if not data_date or not prev_date:
        return observations

    if consensus:
        gaining_consensus = []
        losing_consensus = []
        for stock in consensus:
            delta = _get_stock_weight_change(stock["stock_code"], data_date, prev_date)
            if delta and abs(delta) > 1.0:
                if delta > 0:
                    gaining_consensus.append((stock, delta))
                else:
                    losing_consensus.append((stock, delta))

        if gaining_consensus:
            gaining_consensus.sort(key=lambda item: item[1], reverse=True)
            names = ", ".join(f"{stock['stock_code']} {stock.get('stock_name') or ''}(+{delta:.1f}%)" for stock, delta in gaining_consensus[:3])
            observations.append(f"共識持股權重上升（exposure，不等於主動加碼）: {names}")

        if losing_consensus:
            losing_consensus.sort(key=lambda item: item[1])
            names = ", ".join(f"{stock['stock_code']} {stock.get('stock_name') or ''}({delta:.1f}%)" for stock, delta in losing_consensus[:3])
            observations.append(f"共識持股權重下降（exposure，不等於主動減碼）: {names}")

    if new_positions:
        significant_new = [position for position in new_positions if position["weight_pct"] >= _CORE_POSITION_WEIGHT]
        if significant_new:
            names = ", ".join(f"{position['stock_code']} {position.get('stock_name') or ''}({position['weight_pct']:.1f}%)" for position in significant_new[:3])
            observations.append(f"重要新增核心部位: {names}")

    if removed_positions:
        significant_removed = [position for position in removed_positions if position["prev_weight_pct"] >= _CORE_POSITION_WEIGHT]
        if significant_removed:
            names = ", ".join(f"{position['stock_code']} {position.get('stock_name') or ''}({position['prev_weight_pct']:.1f}%)" for position in significant_removed[:3])
            observations.append(f"重要移除核心部位: {names}")

    etf_activity = {}
    for mover in top_movers:
        code = mover["etf_code"]
        etf_activity[code] = etf_activity.get(code, 0) + 1
    if etf_activity:
        most_active = sorted(etf_activity.items(), key=lambda item: item[1], reverse=True)[:3]
        if most_active[0][1] >= 3:
            names = ", ".join(f"{code}({count}檔)" for code, count in most_active)
            observations.append(f"權重變動集中 ETF: {names}")

    return observations


def _get_data_warnings(data_date):
    if not data_date:
        return ["⚠️ 無持倉資料"]
    warnings = []
    try:
        actual_count = _get_actual_etf_count(data_date)
        expected_count = get_active_etf_count(as_of_date=data_date)
        if expected_count and actual_count < expected_count:
            warnings.append(
                f"⚠️ 資料不完整: 預期 {expected_count} 檔 ETF，"
                f"實際取得 {actual_count} 檔"
            )

        totals_by_etf = {}
        for row in _canonical_stock_rows(data_date):
            etf_code = row["etf_code"]
            totals_by_etf[etf_code] = totals_by_etf.get(etf_code, 0.0) + (row.get("weight_pct") or 0.0)
        for etf_code, total_weight in sorted(totals_by_etf.items()):
            if total_weight < 80.0:
                warnings.append(f"⚠️ {etf_code}: 股票權重僅 {total_weight:.1f}%，可能資料不完整")

        failed = _get_failed_etfs(data_date)
        if failed:
            warnings.append(f"⚠️ 抓取失敗: {', '.join(failed)}")
    except sqlite3.OperationalError:
        pass

    return warnings


def _get_signals(data_date):
    if not data_date:
        return []
    try:
        with _using_row_factory(_dict_factory) as conn:
            rows = conn.execute("SELECT * FROM etf_manager_signals WHERE date = ?", (data_date,)).fetchall()
        return sorted([row for row in rows if _is_significant_signal(row)], key=_signal_sort_key)
    except sqlite3.OperationalError:
        return []


def _is_significant_signal(row: dict) -> bool:
    return abs(row.get("signal_score") or 0) >= _SIGNIFICANT_SIGNAL_SCORE


def _signal_summary(signals: list[dict]) -> dict:
    signals = [row for row in signals if _is_significant_signal(row)]
    return {
        "fresh_consensus": sum(1 for row in signals if _is_consensus(row) and _freshness(row) == "new"),
        "reversals": sum(1 for row in signals if _freshness(row) == "reversal"),
        "persistent_consensus": sum(1 for row in signals if _is_consensus(row) and _freshness(row) == "persistent"),
        "stale_or_fading": sum(1 for row in signals if _freshness(row) in {"stale", "fading"}),
    }


def _signal_sort_key(row: dict):
    return (
        FRESHNESS_ORDER.get(_freshness(row), 9),
        -abs(row.get("signal_score") or 0),
        row.get("stock_code") or "",
    )


def _is_consensus(row: dict) -> bool:
    return str(row.get("signal_type") or "").startswith("consensus_")


def _freshness(row: dict) -> str:
    return row.get("signal_freshness") or "current"


def _format_signal_line(row: dict) -> str:
    freshness = FRESHNESS_LABELS.get(_freshness(row), _freshness(row))
    issuer_count = row.get("issuer_count") or len(_json_list(row.get("issuers")))
    etf_count = row.get("etf_count") or len(_json_list(row.get("etf_codes")))
    direction = _signal_direction(row)
    avg_active = _avg_active_delta_pct(row)
    avg_active_text = f" | avg activeΔ {avg_active:+.2f}%" if avg_active is not None else ""
    reason = row.get("freshness_reason") or row.get("explanation") or ""
    reason_text = f" | {reason}" if reason else ""
    score = row.get("signal_score") or 0
    return (
        f"{direction} {row.get('stock_code')} {row.get('stock_name') or ''} "
        f"| {row.get('signal_type')} | {freshness} | "
        f"{issuer_count} issuers/{etf_count} ETF | "
        f"score={score:+.0f} | conf={row.get('confidence') or 'normal'}"
        f"{avg_active_text}{reason_text}"
    )


def _signal_direction(row: dict) -> str:
    signal_type = row.get("signal_type") or ""
    score = row.get("signal_score") or 0
    if "reduce" in signal_type or score < 0:
        return "REDUCE"
    if "add" in signal_type or "new" in signal_type or score > 0:
        return "ADD"
    return "NEUTRAL"


def _json_list(value):
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _signal_evidence(row: dict) -> list[dict]:
    evidence = _json_list(row.get("evidence_json"))
    return [item for item in evidence if isinstance(item, dict)]


def _avg_active_delta_pct(row: dict):
    values = [item.get("active_shares_delta_pct_1d") for item in _signal_evidence(row)]
    values = [value for value in values if isinstance(value, (int, float))]
    if not values:
        return None
    return sum(values) / len(values)


def _fmt_pct(value, suffix="%"):
    if value is None:
        return "N/A"
    return f"{value:.2f}{suffix}"


def _fmt_signed(value):
    if value is None:
        return "N/A"
    return f"{value:+.2f}"


def _dict_factory(cursor, row):
    return {column[0]: row[index] for index, column in enumerate(cursor.description)}


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
        "📊 台灣主動 ETF 每日持倉報告",
        f"📅 {now.strftime('%Y-%m-%d %H:%M')} CST",
        "",
        f"**執行日期**: {summary.get('date', 'N/A')}",
        f"**預期資料日期**: {summary.get('expected_data_date') or 'N/A'}",
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
    try:
        with _using_row_factory(None) as conn:
            row = conn.execute("SELECT MAX(date) FROM etf_manager_signals").fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row and row[0] else None
