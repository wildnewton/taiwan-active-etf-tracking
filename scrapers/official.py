import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from config import get_etf_config
from scrapers.moneydj import classify_asset, dedupe_rows, split_rows, validate_rows


SOURCE_TYPE = "official_fallback"
EXTRACTION_METHOD = "requests_bs4"
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
    return response.text


def parse_fubon(html: str, etf_code: str, source_url: str) -> list[dict]:
    return _parse_official_table(html, etf_code, source_url)


def parse_capital(html: str, etf_code: str, source_url: str) -> list[dict]:
    return _parse_official_table(html, etf_code, source_url)


def parse_taishin(html: str, etf_code: str, source_url: str) -> list[dict]:
    return _parse_official_table(html, etf_code, source_url)


def parse_mega(html: str, etf_code: str, source_url: str) -> list[dict]:
    return _parse_official_table(html, etf_code, source_url)


def parse_twse(html: str, etf_code: str, source_url: str) -> list[dict]:
    return _parse_official_table(html, etf_code, source_url)


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
        ok, reason = validate_rows(all_rows)
        stock_rows, non_stock_rows = split_rows(all_rows)
        total_weight_all_rows = _sum_weights(all_rows)
        total_weight_stock_rows = _sum_weights(stock_rows)
    except KeyError:
        try:
            html = fetch_static(source_url)
            all_rows = dedupe_rows(parse_twse(html, etf_code, source_url))
            ok, reason = validate_rows(all_rows)
            stock_rows, non_stock_rows = split_rows(all_rows)
            total_weight_all_rows = _sum_weights(all_rows)
            total_weight_stock_rows = _sum_weights(stock_rows)
        except Exception as exc:
            return {
                "ok": False,
                "reason": str(exc),
                "all_rows": [],
                "stock_rows": [],
                "non_stock_rows": [],
                "source_url": source_url,
                "source_type": SOURCE_TYPE,
                "total_weight_all_rows": 0.0,
                "total_weight_stock_rows": 0.0,
            }
    except Exception as exc:
        return {
            "ok": False,
            "reason": str(exc),
            "all_rows": [],
            "stock_rows": [],
            "non_stock_rows": [],
            "source_url": source_url,
            "source_type": SOURCE_TYPE,
            "total_weight_all_rows": 0.0,
            "total_weight_stock_rows": 0.0,
        }

    return {
        "ok": ok,
        "reason": reason,
        "all_rows": all_rows,
        "stock_rows": stock_rows,
        "non_stock_rows": non_stock_rows,
        "source_url": source_url,
        "source_type": SOURCE_TYPE,
        "total_weight_all_rows": total_weight_all_rows,
        "total_weight_stock_rows": total_weight_stock_rows,
    }


def _parse_official_table(html: str, etf_code: str, source_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    date = _parse_date(soup)
    rows = []

    for table in soup.find_all("table"):
        table_rows = _parse_table_rows(table, etf_code.upper(), source_url, date)
        if table_rows:
            rows.extend(table_rows)

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
        asset_name = f"{stock_name}({stock_code}.TW)"
        classification = classify_asset(asset_name)
        if classification["asset_type"] != "stock":
            continue

        rows.append(
            {
                "date": date,
                "etf_code": etf_code,
                "asset_name": asset_name,
                "asset_type": classification["asset_type"],
                "stock_code": classification["stock_code"],
                "stock_name": classification["stock_name"],
                "shares": shares,
                "weight_pct": weight_pct,
                "source_url": source_url,
                "source_type": SOURCE_TYPE,
                "extraction_method": EXTRACTION_METHOD,
            }
        )

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
    if not code_match:
        return None

    stock_code = code_match.group(1)
    stock_name = name_text.strip()
    shares = _parse_number(shares_text)
    weight_pct = _parse_float(weight_text)
    if not stock_name or weight_pct is None:
        return None

    return stock_code, stock_name, shares, weight_pct


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
    if not cleaned or cleaned in {"-", "--"}:
        return None
    return float(cleaned)


def _parse_number(value: str) -> int | float | None:
    cleaned = value.strip().replace(",", "")
    if not cleaned or cleaned in {"-", "--"}:
        return None

    number = float(cleaned)
    return int(number) if number.is_integer() else number


def _parser_for_issuer(issuer: str):
    parsers = {
        "Fubon": parse_fubon,
        "Capital": parse_capital,
        "Taishin": parse_taishin,
        "Mega": parse_mega,
        "TWSE": parse_twse,
    }
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
