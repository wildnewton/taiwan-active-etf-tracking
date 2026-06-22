"""Daily report generator for Taiwan Active ETF Holdings Scraper."""
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))


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
