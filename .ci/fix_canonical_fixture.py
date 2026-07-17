from pathlib import Path
import sys


path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")

signature = '    source_type="moneydj_primary",\n):'
assert text.count(signature) == 1, "canonical helper signature changed"
text = text.replace(
    signature,
    '    source_type="moneydj_primary",\n    complete=True,\n):',
)

helper_boundary = "\n\n\ndef fetch_changes"
assert text.count(helper_boundary) == 1, "canonical helper boundary changed"
cash_completion = "\n".join(
    [
        "        if complete:",
        "            stock_total = conn.execute(",
        '                """',
        "                SELECT COALESCE(SUM(weight_pct), 0.0)",
        "                FROM etf_daily_holdings",
        "                WHERE date = ? AND etf_code = ? AND source_type = ?",
        '                """,',
        "                (date, etf_code, source_type),",
        "            ).fetchone()[0]",
        "            conn.execute(",
        '                """',
        "                INSERT OR REPLACE INTO etf_daily_non_stock_assets (",
        "                    date, etf_code, asset_name, asset_type, weight_pct,",
        "                    source_url, source_type, extraction_method, scraped_at",
        "                ) VALUES (?, ?, 'Cash', 'cash', ?, 'https://example.test',",
        "                          ?, 'test', '2026-06-24T00:00:00')",
        '                """,',
        "                (date, etf_code, 100.0 - stock_total, source_type),",
        "            )",
        "",
    ]
)
text = text.replace(helper_boundary, "\n" + cash_completion + helper_boundary)

partial_call = (
    '    insert_holding("2026-06-24", "00980A", "2330", "台積電", '
    '100, 10.5, "official_static")'
)
assert text.count(partial_call) == 1, "partial-source fixture call changed"
partial_replacement = "\n".join(
    [
        "    insert_holding(",
        '        "2026-06-24",',
        '        "00980A",',
        '        "2330",',
        '        "台積電",',
        "        100,",
        "        10.5,",
        '        "official_static",',
        "        complete=False,",
        "    )",
    ]
)
text = text.replace(partial_call, partial_replacement)
path.write_text(text, encoding="utf-8")
