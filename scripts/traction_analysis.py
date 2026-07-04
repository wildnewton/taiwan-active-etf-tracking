"""Traction analysis for Taiwan Active ETF holdings.

Identifies which stocks are gaining or losing traction among fund managers
based on active_add / active_reduce signals (not passive weight changes).

Usage:
    python3 scripts/traction_analysis.py
    python3 scripts/traction_analysis.py --db data/active_etf_holdings.sqlite
    python3 scripts/traction_analysis.py --window 7 --report-dir reports
"""
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

import db

CST = timezone(timedelta(hours=8))

# Minimum instances for a stock to appear in the report
_MIN_ACTIVE_THRESHOLD = 2


def _get_latest_date(conn):
    row = conn.execute(
        "SELECT MAX(date) FROM etf_holding_changes"
    ).fetchone()
    return row[0] if row and row[0] else None


def _get_date_range(conn, window_days, latest_date):
    """Return (start_date, end_date) for the analysis window."""
    if not latest_date:
        return None, None
    rows = conn.execute(
        """
        SELECT DISTINCT date FROM etf_holding_changes
        WHERE date <= ? ORDER BY date DESC
        """,
        (latest_date,),
    ).fetchall()
    dates = [r[0] for r in rows]
    if len(dates) <= window_days:
        return dates[-1] if dates else None, latest_date
    return dates[window_days], latest_date


def _net_active_flow(conn, start_date, end_date):
    """Calculate net_active_flow per stock over the window."""
    rows = conn.execute(
        """
        WITH active_adds AS (
            SELECT stock_code, stock_name,
                   COUNT(*) AS adds_count,
                   COUNT(DISTINCT issuer) AS add_issuers,
                   SUM(CASE WHEN consecutive_active_add_days >= 2 THEN 1 ELSE 0 END) AS add_streaks
            FROM etf_holding_changes
            WHERE date >= ? AND date <= ?
              AND is_active_add = 1
            GROUP BY stock_code
        ),
        active_reduces AS (
            SELECT h.stock_code, h.stock_name,
                   COUNT(*) AS reduces_count,
                   COUNT(DISTINCT h.issuer) AS reduce_issuers,
                   SUM(CASE WHEN h.consecutive_active_reduce_days >= 2 THEN 1 ELSE 0 END) AS reduce_streaks
            FROM etf_holding_changes h
            WHERE h.date >= ? AND h.date <= ?
              AND h.is_active_reduce = 1
            GROUP BY h.stock_code
        ),
        new_positions AS (
            SELECT stock_code, COUNT(*) AS new_pos_count
            FROM etf_holding_changes
            WHERE date >= ? AND date <= ?
              AND is_new_position = 1
            GROUP BY stock_code
        ),
        removed_positions AS (
            SELECT stock_code, COUNT(*) AS removed_pos_count
            FROM etf_holding_changes
            WHERE date >= ? AND date <= ?
              AND is_removed_position = 1
            GROUP BY stock_code
        ),
        latest_weights AS (
            SELECT stock_code,
                   ROUND(SUM(weight_pct), 2) AS total_weight,
                   ROUND(AVG(weight_pct), 2) AS avg_weight
            FROM etf_holding_changes
            WHERE date = ?
            GROUP BY stock_code
        )
        SELECT
            COALESCE(a.stock_code, r.stock_code) AS stock_code,
            COALESCE(a.stock_name, r.stock_name, '') AS stock_name,
            COALESCE(a.adds_count, 0) AS active_adds,
            COALESCE(r.reduces_count, 0) AS active_reduces,
            COALESCE(n.new_pos_count, 0) AS new_positions,
            COALESCE(rm.removed_pos_count, 0) AS removed_positions,
            COALESCE(a.add_issuers, 0) AS add_issuers,
            COALESCE(r.reduce_issuers, 0) AS reduce_issuers,
            COALESCE(a.add_streaks, 0) AS add_streaks_2plus,
            COALESCE(r.reduce_streaks, 0) AS reduce_streaks_2plus,
            COALESCE(w.total_weight, 0.0) AS total_weight,
            COALESCE(w.avg_weight, 0.0) AS avg_weight
        FROM active_adds a
        FULL OUTER JOIN active_reduces r ON a.stock_code = r.stock_code
        LEFT JOIN new_positions n ON COALESCE(a.stock_code, r.stock_code) = n.stock_code
        LEFT JOIN removed_positions rm ON COALESCE(a.stock_code, r.stock_code) = rm.stock_code
        LEFT JOIN latest_weights w ON COALESCE(a.stock_code, r.stock_code) = w.stock_code
        WHERE COALESCE(a.adds_count, 0) >= ? OR COALESCE(r.reduces_count, 0) >= ?
        ORDER BY (COALESCE(a.adds_count, 0) - COALESCE(r.reduces_count, 0)) DESC
        """,
        (start_date, end_date, start_date, end_date,
         start_date, end_date, start_date, end_date,
         end_date,
         _MIN_ACTIVE_THRESHOLD, _MIN_ACTIVE_THRESHOLD),
    ).fetchall()
    return rows


def _per_etf_breakdown(conn, stock_code, start_date, end_date):
    """Get per-ETF breakdown of active actions for a specific stock."""
    rows = conn.execute(
        """
        SELECT h.date, h.etf_code, u.name, h.issuer,
               h.position_change_type, h.active_direction,
               ROUND(h.weight_pct, 2) AS weight,
               ROUND(h.active_shares_delta_1d, 0) AS shares_delta
        FROM etf_holding_changes h
        JOIN etf_universe u ON h.etf_code = u.code
        WHERE h.stock_code = ?
          AND h.date >= ? AND h.date <= ?
          AND (h.is_active_add = 1 OR h.is_active_reduce = 1)
        ORDER BY h.date, h.etf_code
        """,
        (stock_code, start_date, end_date),
    ).fetchall()
    return rows


def _format_traction_report(rows, start_date, end_date, latest_date):
    """Format the traction analysis into a clean text report."""
    lines = []
    now = datetime.now(CST)
    lines.append("📊 主動ETF 籌碼動能分析（資料區 — 供 AI 分析使用）")
    lines.append(f"📅 {now.strftime('%Y-%m-%d %H:%M')} CST")
    lines.append(f"分析區間: {start_date} → {end_date} (最新資料日: {latest_date})")
    lines.append("=" * 60)
    lines.append("")

    if not rows:
        lines.append("⚠️ 此區間無足夠 active_add/reduce 數據")
        return "\n".join(lines)

    # Columns: 0=code, 1=name, 2=adds, 3=reduces, 4=new_pos, 5=rem_pos,
    #          6=add_iss, 7=red_iss, 8=add_str, 9=red_str, 10=total_w, 11=avg_w
    # Display filter: at least 2 issuers involved, OR strong net flow
    gaining = [
        r for r in rows
        if (r[2] - r[3]) >= 2
        and (r[6] >= 2 or (r[2] - r[3]) >= 4)
    ]
    losing = [
        r for r in rows
        if (r[2] - r[3]) <= -2
        and (r[7] >= 2 or (r[2] - r[3]) <= -4)
    ]
    split = [
        r for r in rows
        if -1 <= (r[2] - r[3]) <= 1
        and r[2] >= 3 and r[3] >= 2
    ]

    # --- GAINING TRACTION ---
    lines.append("🔥 正在獲得關注（主動買入 >> 主動賣出）")
    lines.append("-" * 60)
    if not gaining:
        lines.append("  （無符合條件的標的）")
    else:
        for r in gaining:
            code, name = r[0], r[1]
            adds, reduces = r[2], r[3]
            new_pos, rem_pos = r[4], r[5]
            add_iss, red_iss = r[6], r[7]
            add_str, red_str = r[8], r[9]
            total_w, avg_w = r[10], r[11]
            net = adds - reduces
            label = "🔥" if net >= 6 else "📈" if net >= 4 else "🌱"
            lines.append(
                f"  {label} {code} {name:　<6}  "
                f"NET: +{net:>2d}  "
                f"加{adds:>2d}次({add_iss}家)  "
                f"減{reduces:>2d}次({red_iss}家)  "
                f"總權重{total_w:>5.1f}% | 平均{avg_w:>.1f}%"
            )
            details = []
            if new_pos:
                details.append(f"新增持倉{new_pos}檔ETF")
            if add_str:
                details.append(f"連加{add_str}次")
            if details:
                lines.append(f"     {' | '.join(details)}")
        lines.append("")

    # --- LOSING TRACTION ---
    lines.append("❄️ 正在失去關注（主動賣出 >> 主動買入）")
    lines.append("-" * 60)
    if not losing:
        lines.append("  （無符合條件的標的）")
    else:
        for r in losing:
            code, name = r[0], r[1]
            adds, reduces = r[2], r[3]
            new_pos, rem_pos = r[4], r[5]
            add_iss, red_iss = r[6], r[7]
            add_str, red_str = r[8], r[9]
            total_w, avg_w = r[10], r[11]
            net = adds - reduces
            label = "❄️" if net <= -6 else "📉" if net <= -4 else "🌬️"
            lines.append(
                f"  {label} {code} {name:　<6}  "
                f"NET: {net:>3d}  "
                f"加{adds:>2d}次({add_iss}家)  "
                f"減{reduces:>2d}次({red_iss}家)  "
                f"總權重{total_w:>5.1f}% | 平均{avg_w:>.1f}%"
            )
            details = []
            if rem_pos:
                details.append(f"移除持倉{rem_pos}檔ETF")
            if red_str:
                details.append(f"連減{red_str}次")
            if details:
                lines.append(f"     {' | '.join(details)}")
        lines.append("")

    # --- DISAGREEMENT ---
    lines.append("⚡ 分歧較大（兩邊都有明顯 action）")
    lines.append("-" * 60)
    if not split:
        lines.append("  （無明顯分歧標的）")
    else:
        for r in split:
            code, name = r[0], r[1]
            adds, reduces = r[2], r[3]
            add_iss, red_iss = r[6], r[7]
            total_w, avg_w = r[10], r[11]
            net = adds - reduces
            lines.append(
                f"  ⚡ {code} {name:　<6}  "
                f"NET: {net:+3d}  "
                f"加{adds:>2d}次({add_iss}家)  "
                f"減{reduces:>2d}次({red_iss}家)  "
                f"總權重{total_w:>5.1f}% | 平均{avg_w:>.1f}%"
            )
        lines.append("")

    # --- SUMMARY STATS ---
    lines.append("📋 區間統計")
    lines.append("-" * 60)
    total_unique = len(rows)
    lines.append(f"  有 active action 的股票: {total_unique} 支")
    lines.append(f"  淨買超 (NET≥+2): {len(gaining)} 支")
    lines.append(f"  淨賣超 (NET≤-2): {len(losing)} 支")
    lines.append(f"  分歧明顯: {len(split)} 支")
    lines.append("")
    lines.append("⚠️ 備註：本分析僅統計 confirmed_active_add / confirmed_active_reduce")
    lines.append("   被動權重變化（如股價上漲導致佔比提高）不計入")
    lines.append("   「新/移除持倉」為該 ETF 該股從有到無或無到有")
    lines.append("   總權重 = 該股在所有 ETF 中的持倉權重總和（最新日）")
    lines.append("   平均權重 = 該股在持有它的 ETF 中的平均權重（最新日）")

    return "\n".join(lines)


def generate_traction_report(
    db_path="data/active_etf_holdings.sqlite",
    window_days=5,
    report_dir=None,
) -> str:
    """Generate the traction analysis report."""
    db.init_db(db_path)

    with db._connect() as conn:
        latest_date = _get_latest_date(conn)
        if not latest_date:
            return "⚠️ 無 holding_changes 資料"

        start_date, end_date = _get_date_range(conn, window_days, latest_date)
        if not start_date:
            return f"⚠️ 資料不足 {window_days} 天窗口"

        rows = _net_active_flow(conn, start_date, end_date)
        report = _format_traction_report(rows, start_date, end_date, latest_date)

    return report


def main():
    parser = argparse.ArgumentParser(description="Taiwan Active ETF Traction Analysis")
    parser.add_argument(
        "--db",
        default="data/active_etf_holdings.sqlite",
        help="SQLite database path",
    )
    parser.add_argument(
        "--window",
        type=int,
        default=5,
        help="Trading day analysis window (default: 5)",
    )
    parser.add_argument(
        "--report-dir",
        default=None,
        help="Directory to save report file (omit for stdout only)",
    )
    args = parser.parse_args()

    report = generate_traction_report(
        db_path=args.db,
        window_days=args.window,
    )

    print(report)

    if args.report_dir:
        report_dir = Path(args.report_dir)
        report_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(CST).strftime("%Y%m%d_%H%M%S")
        path = report_dir / f"traction_report_{stamp}.txt"
        path.write_text(report, encoding="utf-8")
        print(f"\n報告已儲存: {path}")


if __name__ == "__main__":
    main()
