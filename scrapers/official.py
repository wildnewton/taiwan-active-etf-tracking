"""Official ETF issuer fallback scrapers.

Official issuer pages are fallback sources. They are not always guaranteed to
expose the same complete all-asset table as MoneyDJ Basic0007B, so this module
uses issuer-specific validation instead of the strict MoneyDJ ~100% full-weight
validation.
"""

import json
import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from config import get_etf_config
from scrapers.moneydj import classify_asset, dedupe_rows, split_rows


SOURCE_TYPE = "official_fallback"
EXTRACTION_METHOD_STATIC = "requests_bs4"
EXTRACTION_METHOD_API = "playwright_api_intercept"
EXTRACTION_METHOD_PLAYWRIGHT = "playwright_table_parse"
EXTRACTION_METHOD_STEALTH = "stealth_playwright_api"

TWSE_URL_TEMPLATE = (
    "https://www.twse.com.tw/zh/products/securities/etf/products/content.html?{code}="
)


def get_official_config(etf_code: str) -> dict:
    etf = get_etf_config(etf_code.upper())
    internal_ids = _parse_official_logic(etf.get("official_logic", ""))
    return {
        "code": etf["code"],
        "issuer": etf["issuer"],
        "name": etf["name"],
        "url": etf["official_url"],
        "method": etf["official_method"],
        "official_logic": etf.get("official_logic"),
        "internal_id": next(iter(internal_ids.values()), None),
        "internal_ids": internal_ids,
    }


def fetch_static(url: str, timeout: int = 30) -> str:
    parsed = urlparse(url)
    referer = f"{parsed.scheme}://{parsed.netloc}/" if parsed.scheme and parsed.netloc else url
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": referer,
    }
    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return response.text


# Static parsers

def parse_fubon(html: str, etf_code: str, source_url: str) -> list[dict]:
    return _parse_official_table(html, etf_code, source_url)


def parse_taishin(html: str, etf_code: str, source_url: str) -> list[dict]:
    return _parse_official_table(html, etf_code, source_url)


def parse_twse(html: str, etf_code: str, source_url: str) -> list[dict]:
    return _parse_official_table(html, etf_code, source_url)


# API / text parsers

def parse_capital_api(buyback_json: str, etf_code: str, source_url: str) -> list[dict]:
    """Parse Capital's buyback API response.

    The observed payload uses data.stocks[] with keys such as stocNo, stocName,
    share, weight, and weightRound. Keep fallbacks for nearby naming variants.
    """
    data = json.loads(buyback_json)
    payload = data.get("data", {}) if isinstance(data, dict) else {}
    stocks = payload.get("stocks", []) if isinstance(payload, dict) else []
    date = None
    pcf = payload.get("pcf", {}) if isinstance(payload, dict) else {}
    if isinstance(pcf, dict):
        date = pcf.get("date2") or pcf.get("date1")
    date = _normalize_date(date)

    rows = []
    for item in stocks:
        if not isinstance(item, dict):
            continue
        code = str(
            item.get("stocNo")
            or item.get("stockNo")
            or item.get("stockCode")
            or item.get("code")
            or ""
        ).strip()
        name = str(
            item.get("stocName")
            or item.get("stockName")
            or item.get("name")
            or ""
        ).strip()
        shares = _parse_number(str(item.get("share") or item.get("shares") or item.get("qty") or ""))
        weight = _parse_float(str(item.get("weightRound") or item.get("weight") or item.get("ratio") or ""))
        code_match = re.search(r"\b(\d{4})\b", code)
        if not code_match or not name or weight is None:
            continue
        rows.append(_row(etf_code, code_match.group(1), name, shares, weight, source_url, date, EXTRACTION_METHOD_API))
    return rows


def parse_nomura_api(assets_json: str, etf_code: str, source_url: str) -> list[dict]:
    data = json.loads(assets_json)
    fund_data = data.get("Entries", {}).get("Data", {})
    nav_date = fund_data.get("FundAsset", {}).get("NavDate")
    date = _normalize_date(nav_date)

    rows = []
    for table in fund_data.get("Table", []):
        if table.get("TableTitle") != "股票":
            continue
        for raw in table.get("Rows", []):
            if len(raw) < 4:
                continue
            code, name, shares, weight = raw[:4]
            code_match = re.search(r"\b(\d{4})\b", str(code))
            parsed_weight = _parse_float(str(weight))
            if not code_match or not name or parsed_weight is None:
                continue
            rows.append(
                _row(
                    etf_code,
                    code_match.group(1),
                    str(name).strip(),
                    _parse_number(str(shares)),
                    parsed_weight,
                    source_url,
                    date,
                    EXTRACTION_METHOD_STEALTH,
                )
            )
    return rows


def parse_mega_text(body_text: str, etf_code: str, source_url: str, date: str | None = None) -> list[dict]:
    if not date:
        match = re.search(r"(\d{4}/\d{2}/\d{2})", body_text)
        date = match.group(1) if match else None

    rows = []
    lines = [line.strip() for line in body_text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if not re.fullmatch(r"\d{4}", line):
            continue
        try:
            code = line
            name = lines[index + 1]
            shares = _parse_number(lines[index + 2])
            weight = _parse_float(lines[index + 3])
        except IndexError:
            continue
        if not name or weight is None:
            continue
        rows.append(_row(etf_code, code, name, shares, weight, source_url, date, EXTRACTION_METHOD_PLAYWRIGHT))
    return rows


def parse_uni_president_table(
    table_rows: list[list[str]],
    etf_code: str,
    source_url: str,
    date: str | None = None,
) -> list[dict]:
    rows = []
    for cells in table_rows:
        if len(cells) < 4:
            continue
        code, name, shares, weight = cells[:4]
        code_match = re.search(r"\b(\d{4})\b", str(code))
        parsed_weight = _parse_float(str(weight))
        if not code_match or not name or parsed_weight is None:
            continue
        rows.append(
            _row(
                etf_code,
                code_match.group(1),
                str(name).strip(),
                _parse_number(str(shares)),
                parsed_weight,
                source_url,
                date,
                EXTRACTION_METHOD_PLAYWRIGHT,
            )
        )
    return rows


# Browser / API scraper functions

async def scrape_capital_playwright(etf_code: str, page) -> dict:
    etf_code = etf_code.upper()
    config = get_official_config(etf_code)
    source_url = config["url"]
    buyback_body = None

    async def on_response(response):
        nonlocal buyback_body
        if "buyback" in response.url.lower() and "capitalfund" in response.url.lower():
            try:
                buyback_body = await response.text()
            except Exception:
                pass

    page.on("response", on_response)
    await page.goto(source_url, wait_until="networkidle", timeout=60000)
    await page.wait_for_timeout(3000)
    page.remove_listener("response", on_response)

    if not buyback_body:
        return _failed_result(source_url, "Capital buyback API not intercepted")

    all_rows = dedupe_rows(parse_capital_api(buyback_body, etf_code, source_url))
    return _build_result(all_rows, source_url, EXTRACTION_METHOD_API)


async def scrape_nomura_stealth(etf_code: str, page) -> dict:
    etf_code = etf_code.upper()
    config = get_official_config(etf_code)
    source_url = config["url"]
    assets_body = None

    async def on_response(response):
        nonlocal assets_body
        if "GetFundAssets" in response.url:
            try:
                assets_body = await response.text()
            except Exception:
                pass

    page.on("response", on_response)
    await page.goto(source_url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(8000)
    page.remove_listener("response", on_response)

    if not assets_body:
        return _failed_result(source_url, "Nomura GetFundAssets API not intercepted")

    all_rows = dedupe_rows(parse_nomura_api(assets_body, etf_code, source_url))
    return _build_result(all_rows, source_url, EXTRACTION_METHOD_STEALTH)


async def scrape_mega_playwright(etf_code: str, page) -> dict:
    etf_code = etf_code.upper()
    config = get_official_config(etf_code)
    source_url = config["url"]

    await page.goto(source_url, wait_until="networkidle", timeout=60000)
    await page.wait_for_timeout(3000)
    body_text = await page.locator("body").inner_text()

    all_rows = dedupe_rows(parse_mega_text(body_text, etf_code, source_url))
    return _build_result(all_rows, source_url, EXTRACTION_METHOD_PLAYWRIGHT)


async def scrape_uni_president_playwright(etf_code: str, page) -> dict:
    etf_code = etf_code.upper()
    config = get_official_config(etf_code)
    source_url = config["url"]

    await page.goto(source_url, wait_until="networkidle", timeout=60000)

    tables = await page.query_selector_all("table")
    table_data = []
    date = None
    for table in tables:
        rows = await table.query_selector_all("tr")
        if len(rows) < 20:
            continue
        first_row_text = await rows[0].inner_text()
        if "股票" not in first_row_text:
            continue
        for row in rows[1:]:
            cells = await row.query_selector_all("td")
            cell_texts = [(await cell.inner_text()).strip() for cell in cells]
            if len(cell_texts) >= 4:
                table_data.append(cell_texts[:4])
        body_text = await page.locator("body").inner_text()
        date_match = re.search(r"(\d{4}/\d{2}/\d{2})", body_text)
        if date_match:
            date = date_match.group(1)
        break

    if not table_data:
        return _failed_result(source_url, "Uni-President holdings table not found")

    all_rows = dedupe_rows(parse_uni_president_table(table_data, etf_code, source_url, date))
    return _build_result(all_rows, source_url, EXTRACTION_METHOD_PLAYWRIGHT)


# Unified entry points

def scrape_official_static(etf_code: str) -> dict:
    etf_code = etf_code.upper()
    source_url = _build_twse_url(etf_code)

    try:
        config = get_official_config(etf_code)
        if config["method"] == "static":
            source_url = config["url"]
            parser = _parser_for_issuer(config["issuer"])
        else:
            parser = parse_twse
        html = fetch_static(source_url)
        all_rows = dedupe_rows(parser(html, etf_code, source_url))
        return _build_result(all_rows, source_url, EXTRACTION_METHOD_STATIC)
    except KeyError:
        try:
            html = fetch_static(source_url)
            all_rows = dedupe_rows(parse_twse(html, etf_code, source_url))
            return _build_result(all_rows, source_url, EXTRACTION_METHOD_STATIC)
        except Exception as exc:
            return _failed_result(source_url, str(exc))
    except Exception as exc:
        return _failed_result(source_url, str(exc))


async def scrape_official_with_browser(etf_code: str, page) -> dict:
    etf_code = etf_code.upper()
    config = get_official_config(etf_code)
    method = config["method"]
    issuer = config["issuer"]

    if method == "api" and issuer == "Capital":
        return await scrape_capital_playwright(etf_code, page)
    if method == "stealth_api" and issuer == "Nomura":
        return await scrape_nomura_stealth(etf_code, page)
    if method == "playwright" and issuer == "Mega":
        return await scrape_mega_playwright(etf_code, page)
    if method == "playwright" and issuer == "Uni-President":
        return await scrape_uni_president_playwright(etf_code, page)

    return _failed_result(config["url"], f"No browser official scraper for {issuer} (method={method})")


# Internal helpers

def _parse_official_table(html: str, etf_code: str, source_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    date = _parse_date(soup)
    rows = []
    for table in soup.find_all("table"):
        rows.extend(_parse_table_rows(table, etf_code.upper(), source_url, date))
    return rows


def _parse_table_rows(table, etf_code: str, source_url: str, date: str | None) -> list[dict]:
    trs = table.find_all("tr")
    if not trs:
        return []

    header_map = {}
    data_trs = trs
    first_cells = trs[0].find_all(["th", "td"])
    if first_cells and (trs[0].find("th") or _looks_like_header(first_cells)):
        headers = [_normalize_header(cell.get_text(" ", strip=True)) for cell in first_cells]
        header_map = _build_header_map(headers)
        data_trs = trs[1:]

    rows = []
    for tr in data_trs:
        cells = [cell.get_text(" ", strip=True) for cell in tr.find_all("td")]
        if len(cells) < 4:
            continue
        values = _extract_cells(cells, header_map)
        if not values:
            continue
        stock_code, stock_name, shares, weight_pct = values
        rows.append(_row(etf_code, stock_code, stock_name, shares, weight_pct, source_url, date, EXTRACTION_METHOD_STATIC))
    return rows


def _extract_cells(cells: list[str], header_map: dict) -> tuple | None:
    if header_map:
        try:
            code_text = cells[header_map["code"]]
            name_text = cells[header_map["name"]]
            shares_text = cells[header_map["shares"]]
            weight_text = cells[header_map["weight"]]
        except (IndexError, KeyError):
            return None
    else:
        code_text, name_text, shares_text, weight_text = cells[:4]

    code_match = re.search(r"\b(\d{4})\b", code_text)
    stock_name = name_text.strip()
    shares = _parse_number(shares_text)
    weight_pct = _parse_float(weight_text)
    if not code_match or not stock_name or weight_pct is None:
        return None
    return code_match.group(1), stock_name, shares, weight_pct


def _row(etf_code, stock_code, stock_name, shares, weight_pct, source_url, date, method):
    asset_name = f"{stock_name}({stock_code}.TW)"
    classification = classify_asset(asset_name)
    return {
        "date": date,
        "etf_code": etf_code.upper(),
        "asset_name": asset_name,
        "asset_type": classification["asset_type"],
        "stock_code": classification["stock_code"],
        "stock_name": classification["stock_name"],
        "shares": shares,
        "weight_pct": weight_pct,
        "source_url": source_url,
        "source_type": SOURCE_TYPE,
        "extraction_method": method,
    }


def _build_header_map(headers: list[str]) -> dict:
    field_patterns = {
        "code": ("股票代號", "股票代碼", "證券代號", "代號", "code"),
        "name": ("股票名稱", "證券名稱", "名稱", "name"),
        "shares": ("持有股數", "持股數", "庫存股數", "股數", "shares"),
        "weight": ("權重", "投資比例", "佔基金淨資產比例", "比例", "weight", "%"),
    }
    header_map = {}
    for field, patterns in field_patterns.items():
        for index, header in enumerate(headers):
            if any(pattern in header for pattern in patterns):
                header_map[field] = index
                break
    return header_map


def _looks_like_header(cells) -> bool:
    text = " ".join(cell.get_text(" ", strip=True) for cell in cells)
    header_terms = ("股票", "證券", "代號", "名稱", "股數", "權重", "比例", "code", "name")
    return any(term in text.lower() for term in header_terms)


def _normalize_header(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def _normalize_date(value):
    if not value:
        return None
    return str(value).replace("-", "/")


def _parse_date(soup: BeautifulSoup) -> str | None:
    text = soup.get_text(" ", strip=True)
    data_date_match = re.search(
        r"(?:資料日期|日期)\s*[:：]?\s*(\d{4}/\d{2}/\d{2})",
        text,
    )
    if data_date_match:
        return data_date_match.group(1)
    date_match = re.search(r"\d{4}/\d{2}/\d{2}", text)
    return date_match.group(0) if date_match else None


def _parse_float(value: str) -> float | None:
    cleaned = value.strip().replace(",", "").replace("%", "")
    if not cleaned or cleaned.upper() in {"-", "--", "N/A", "NA"}:
        return None
    return float(cleaned)


def _parse_number(value: str) -> int | float | None:
    cleaned = value.strip().replace(",", "")
    if not cleaned or cleaned.upper() in {"-", "--", "N/A", "NA"}:
        return None
    number = float(cleaned)
    return int(number) if number.is_integer() else number


def _parser_for_issuer(issuer: str):
    parsers = {"Fubon": parse_fubon, "Taishin": parse_taishin, "TWSE": parse_twse}
    try:
        return parsers[issuer]
    except KeyError as exc:
        raise ValueError(f"No static official parser for issuer: {issuer}") from exc


def _parse_official_logic(logic: str) -> dict:
    internal_ids = {}
    for part in logic.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        internal_ids[key.strip()] = value.strip()
    return internal_ids


def _build_twse_url(etf_code: str) -> str:
    return TWSE_URL_TEMPLATE.format(code=etf_code.upper())


def _sum_weights(rows: list) -> float:
    return round(sum(row["weight_pct"] for row in rows if row.get("weight_pct") is not None), 2)


def _validate_official_rows(rows: list) -> tuple[bool, str]:
    if not rows:
        return False, "empty rows"
    if len(rows) < 5:
        return False, "fewer than 5 rows"
    if any(not row.get("date") for row in rows):
        return False, "missing date"
    if any(row.get("weight_pct") is None for row in rows):
        return False, "missing weight_pct"

    total_weight = _sum_weights(rows)
    if total_weight < 20.0 or total_weight > 110.0:
        return False, f"official weight out of range: {total_weight:.2f}"

    stock_rows = [row for row in rows if row.get("asset_type") == "stock"]
    if len(stock_rows) < 5:
        return False, "fewer than 5 Taiwan stock rows"
    for row in stock_rows:
        stock_code = row.get("stock_code")
        stock_name = row.get("stock_name")
        if not stock_name or not re.fullmatch(r"\d{4}", str(stock_code or "")):
            return False, "invalid Taiwan stock row"
    return True, "ok"


def _failed_result(source_url: str, reason: str) -> dict:
    return {
        "ok": False,
        "reason": reason,
        "all_rows": [],
        "stock_rows": [],
        "non_stock_rows": [],
        "source_url": source_url,
        "source_type": SOURCE_TYPE,
        "total_weight_all_rows": 0.0,
        "total_weight_stock_rows": 0.0,
    }


def _build_result(all_rows: list, source_url: str, extraction_method: str) -> dict:
    ok, reason = _validate_official_rows(all_rows)
    stock_rows, non_stock_rows = split_rows(all_rows)
    return {
        "ok": ok,
        "reason": reason,
        "all_rows": all_rows,
        "stock_rows": stock_rows,
        "non_stock_rows": non_stock_rows,
        "source_url": source_url,
        "source_type": SOURCE_TYPE,
        "total_weight_all_rows": _sum_weights(all_rows),
        "total_weight_stock_rows": _sum_weights(stock_rows),
    }
