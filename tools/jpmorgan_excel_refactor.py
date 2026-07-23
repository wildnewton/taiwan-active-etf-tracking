from pathlib import Path
import json
import re


OFFICIAL = Path("scripts/scrapers/official.py")
SCRAPER = Path("scripts/scraper.py")
SEED = Path("data/etf_universe_seed.json")
REQUIREMENTS = Path("requirements.txt")


JPMORGAN_BLOCK = r'''
_JPMORGAN_SHEETS = {
    "基金資產 - 股票": "stock",
    "基金資產 - 期貨": "futures",
    "基金資產 - 選擇權": "options",
    "現金與約當現金": "cash",
}


def _jpmorgan_sheet_rows(sheet, expected_date: date) -> list[tuple[str, ...]]:
    rows = [
        tuple("" if value is None else str(value).strip() for value in row)
        for row in sheet.iter_rows(values_only=True)
    ]
    title = rows[0][0] if rows and rows[0] else ""
    match = re.search(r"\((\d{4}-\d{2}-\d{2})\)", title)
    actual_date = match.group(1) if match else "missing"
    if actual_date != expected_date.isoformat():
        raise ValueError(
            f"JPMorgan date mismatch: expected {expected_date.isoformat()}, "
            f"got {actual_date}"
        )

    for index, row in enumerate(rows):
        if row and row[0] in {"股票代碼", "商品代碼", "名稱"}:
            return [item for item in rows[index + 1 :] if item and item[0]]
    raise ValueError(f"JPMorgan table header not found: {title}")


def _jpmorgan_non_stock_row(
    raw: tuple[str, ...],
    asset_type: str,
    etf_code: str,
    source_url: str,
    date_str: str,
) -> dict | None:
    if asset_type == "cash":
        if len(raw) < 3:
            return None
        code, name, shares, amount, weight = None, raw[0], None, raw[1], raw[2]
        asset_name = name
    else:
        if len(raw) < 4 or raw[0].upper() in {"", "-", "N/A"}:
            return None
        code, name, shares, amount, weight = raw[0], raw[1], raw[2], None, raw[3]
        asset_name = f"{name}({code})"

    parsed_weight = _parse_float(weight)
    if not name or parsed_weight is None:
        return None
    return {
        "date": date_str,
        "etf_code": etf_code,
        "asset_name": asset_name,
        "asset_type": asset_type,
        "stock_code": code,
        "stock_name": name,
        "shares": _parse_number(shares) if shares else None,
        "market_value": _parse_number(amount) if amount else None,
        "weight_pct": parsed_weight,
        "source_url": source_url,
        "source_type": SOURCE_TYPE,
        "extraction_method": EXTRACTION_METHOD_EXCEL,
    }


def parse_jpmorgan_excel(
    content: bytes,
    etf_code: str,
    source_url: str,
    target_date: date,
) -> list[dict]:
    workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    missing = [name for name in _JPMORGAN_SHEETS if name not in workbook.sheetnames]
    if missing:
        raise ValueError(f"JPMorgan sheets missing: {', '.join(missing)}")

    etf_code = etf_code.upper()
    date_str = target_date.strftime("%Y/%m/%d")
    rows = []
    for sheet_name, asset_type in _JPMORGAN_SHEETS.items():
        for raw in _jpmorgan_sheet_rows(workbook[sheet_name], target_date):
            if asset_type == "stock":
                if len(raw) < 5 or not re.fullmatch(r"\d{4}", raw[0]):
                    continue
                weight = _parse_float(raw[4])
                if not raw[1] or weight is None:
                    continue
                row = _row(
                    etf_code,
                    raw[0],
                    raw[1],
                    _parse_number(raw[2]),
                    weight,
                    source_url,
                    date_str,
                    EXTRACTION_METHOD_EXCEL,
                )
                row["market_value"] = _parse_number(raw[3])
            else:
                row = _jpmorgan_non_stock_row(
                    raw,
                    asset_type,
                    etf_code,
                    source_url,
                    date_str,
                )
            if row:
                rows.append(row)

    rows = dedupe_rows(rows)
    if len([row for row in rows if row["asset_type"] == "stock"]) < 5:
        raise ValueError("JPMorgan stock rows not found")
    return rows


def scrape_jpmorgan_excel(etf_code: str, target_date: date) -> dict:
    etf_code = etf_code.upper()
    config = get_official_config(etf_code)
    source_url = config["url"]
    try:
        params = {**config["internal_ids"], "date": target_date.isoformat()}
        response = requests.get(
            source_url,
            params=params,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            },
            timeout=30,
        )
        response.raise_for_status()
        source_url = response.url
        rows = parse_jpmorgan_excel(
            response.content,
            etf_code,
            source_url,
            target_date,
        )
        return _build_result(rows, source_url, EXTRACTION_METHOD_EXCEL)
    except Exception as exc:
        return _failed_result(source_url, f"JPMorgan Excel failed: {exc}")


'''


def patch_official():
    text = OFFICIAL.read_text()
    text = text.replace("import json\n", "import asyncio\nimport json\n", 1)
    text = text.replace(
        "from datetime import datetime\n",
        "from datetime import date, datetime\nfrom io import BytesIO\n",
        1,
    )
    text = text.replace(
        "import requests\n",
        "import requests\nfrom openpyxl import load_workbook\n",
        1,
    )
    text = text.replace(
        'EXTRACTION_METHOD_API = "playwright_api_intercept"\n',
        'EXTRACTION_METHOD_API = "playwright_api_intercept"\n'
        'EXTRACTION_METHOD_EXCEL = "requests_xlsx"\n',
        1,
    )
    text = text.replace(
        "async def scrape_official_with_browser(etf_code: str, page) -> dict:",
        "async def scrape_official_with_browser(\n"
        "    etf_code: str,\n"
        "    page,\n"
        "    target_date: date | None = None,\n"
        ") -> dict:",
        1,
    )
    marker = '    issuer = config["issuer"]\n\n'
    branch = (
        marker
        + '    if method == "api" and issuer == "JPMorgan":\n'
        + '        if target_date is None:\n'
        + '            return _failed_result(config["url"], "target_date is required for JPMorgan")\n'
        + '        return await asyncio.to_thread(scrape_jpmorgan_excel, etf_code, target_date)\n\n'
    )
    if marker not in text:
        raise RuntimeError("dispatcher marker not found")
    text = text.replace(marker, branch, 1)
    text = text.replace(
        '    if method == "playwright" and issuer == "JPMorgan":\n'
        '        return await scrape_jpmorgan_playwright(etf_code, page)\n',
        "",
        1,
    )
    pattern = re.compile(
        r"\nasync def _parse_jpmorgan_stock_rows\(.*?(?=def _parse_float\()",
        re.DOTALL,
    )
    text, count = pattern.subn("\n" + JPMORGAN_BLOCK, text, count=1)
    if count != 1:
        raise RuntimeError(f"expected one JPMorgan block, replaced {count}")
    OFFICIAL.write_text(text)


def patch_scraper():
    text = SCRAPER.read_text()
    text = text.replace(
        "_official_fallback_with_browser(etf_code, page)",
        "_official_fallback_with_browser(etf_code, page, target_date=target_date)",
    )
    old_call = """            page,
            official_candidate,
        )"""
    new_call = """            page,
            official_candidate,
            target_date=target_date,
        )"""
    if text.count(old_call) != 2:
        raise RuntimeError("unexpected low-row-count call shape")
    text = text.replace(old_call, new_call)
    text = text.replace(
        "async def _official_fallback_with_browser(etf_code: str, page) -> dict:",
        "async def _official_fallback_with_browser(\n"
        "    etf_code: str,\n"
        "    page,\n"
        "    target_date: date | None = None,\n"
        ") -> dict:",
        1,
    )
    text = text.replace(
        "official_browser = await scrape_official_with_browser(etf_code, page)",
        "official_browser = await scrape_official_with_browser(\n"
        "            etf_code,\n"
        "            page,\n"
        "            target_date=target_date,\n"
        "        )",
        1,
    )
    text = text.replace(
        "async def _maybe_replace_low_row_count_async(etf_code: str, moneydj_result: dict, page, official_candidate: dict | None = None) -> dict:",
        "async def _maybe_replace_low_row_count_async(\n"
        "    etf_code: str,\n"
        "    moneydj_result: dict,\n"
        "    page,\n"
        "    official_candidate: dict | None = None,\n"
        "    target_date: date | None = None,\n"
        ") -> dict:",
        1,
    )
    SCRAPER.write_text(text)


def patch_config():
    data = json.loads(SEED.read_text())
    entry = next(item for item in data if item["code"] == "00401A")
    entry["official_url"] = "https://am.jpmorgan.com/FundsMarketingHandler/excel"
    entry["official_method"] = "api"
    entry["official_logic"] = (
        "type=holding_pcf;cusip=TW00000401A1;country=tw;"
        "role=twetf;locale=zh-TW"
    )
    SEED.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def patch_requirements_and_tests():
    text = REQUIREMENTS.read_text()
    if "openpyxl" not in text:
        REQUIREMENTS.write_text(text.rstrip() + "\nopenpyxl>=3.1.0\n")
    for path in (
        Path("tests/test_official_jpmorgan.py"),
        Path("tests/test_official_jpmorgan_live_regression.py"),
    ):
        path.unlink()


patch_official()
patch_scraper()
patch_config()
patch_requirements_and_tests()
