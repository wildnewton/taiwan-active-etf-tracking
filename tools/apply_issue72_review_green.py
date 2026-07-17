from pathlib import Path


official_path = Path("scripts/scrapers/official.py")
official = official_path.read_text(encoding="utf-8")

official = official.replace(
    'EXTRACTION_METHOD_API = "playwright_api_intercept"\n',
    'EXTRACTION_METHOD_API = "playwright_api_intercept"\n'
    'EXTRACTION_METHOD_API_REQUEST = "playwright_api_request"\n',
    1,
)

official = official.replace(
    '''def parse_allianz_fund_options(options_json: str, etf_code: str) -> str:
    """Return Allianz's internal FundNo for one exact exchange ETF code."""
    data = json.loads(options_json)
    entries = data.get("Entries", []) if isinstance(data, dict) else []
''',
    '''def parse_allianz_fund_options(options_json: str, etf_code: str) -> str:
    """Return Allianz's internal FundNo for one exact exchange ETF code."""
    data = json.loads(options_json)
    if not isinstance(data, dict) or data.get("StatusCode") != 0:
        message = data.get("Message") if isinstance(data, dict) else "invalid payload"
        raise ValueError(f"Allianz fund options API failed: {message}")
    entries = data.get("Entries", [])
''',
    1,
)

official = official.replace(
    '''    data = json.loads(trade_json)
    entries = data.get("Entries", {}) if isinstance(data, dict) else {}
    if not isinstance(entries, dict):
''',
    '''    data = json.loads(trade_json)
    if not isinstance(data, dict) or data.get("StatusCode") != 0:
        message = data.get("Message") if isinstance(data, dict) else "invalid payload"
        raise ValueError(f"Allianz trade info API failed: {message}")
    entries = data.get("Entries", {})
    if not isinstance(entries, dict):
''',
    1,
)

official = official.replace(
    '''    stock_table = None
    for table in entries.get("DynamicTableData", []):
''',
    '''    tables = entries.get("DynamicTableData")
    if not isinstance(tables, list):
        raise ValueError("Allianz holdings tables missing")

    stock_table = None
    for table in tables:
''',
    1,
)

official = official.replace(
    '''    columns = stock_table.get("Columns", [])
    headers = [
''',
    '''    columns = stock_table.get("Columns")
    if not isinstance(columns, list):
        raise ValueError("Allianz stock table schema invalid")
    headers = [
''',
    1,
)

official = official.replace(
    '''    rows = []
    for raw in stock_table.get("Rows", []):
''',
    '''    raw_rows = stock_table.get("Rows")
    if not isinstance(raw_rows, list):
        raise ValueError("Allianz stock table rows invalid")

    rows = []
    for raw in raw_rows:
''',
    1,
)

parser_start = official.index("def parse_allianz_api(")
parser_end = official.index("def parse_mega_text(", parser_start)
parser = official[parser_start:parser_end].replace(
    "EXTRACTION_METHOD_API,",
    "EXTRACTION_METHOD_API_REQUEST,",
)
official = official[:parser_start] + parser + official[parser_end:]

handler_start = official.index("async def scrape_allianz_playwright(")
handler_end = official.index("async def scrape_mega_playwright(", handler_start)
handler = official[handler_start:handler_end].replace(
    "return _build_result(all_rows, source_url, EXTRACTION_METHOD_API)",
    "return _build_result(all_rows, source_url, EXTRACTION_METHOD_API_REQUEST)",
)
official = official[:handler_start] + handler + official[handler_end:]

official = official.replace(
    "return dedupe_rows(rows)\n\n\n\ndef parse_allianz_fund_options",
    "return dedupe_rows(rows)\n\n\ndef parse_allianz_fund_options",
    1,
)
official = official.replace(
    "return _build_result(all_rows, source_url, EXTRACTION_METHOD_API)\n\n\n\ndef _allianz_api_url",
    "return _build_result(all_rows, source_url, EXTRACTION_METHOD_API)\n\n\ndef _allianz_api_url",
    1,
)

official_path.write_text(official, encoding="utf-8")
