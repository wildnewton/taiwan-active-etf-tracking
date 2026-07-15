import re

import requests
from bs4 import BeautifulSoup


MONEYDJ_URL_TEMPLATE = (
    "https://www.moneydj.com/ETF/X/Basic/Basic0007B.xdjhtm?etfid={code}.TW"
)
SOURCE_TYPE = "moneydj_primary"
EXTRACTION_METHOD = "requests_bs4"

# Basic0007B is the full-holdings page. Weight-quality warnings must use all
# parsed rows, including non-stock assets, because the total should be near 100%.
WARNING_MIN_TOTAL_WEIGHT = 70.0
WARNING_MAX_TOTAL_WEIGHT = 140.0


def build_moneydj_url(etf_code: str) -> str:
    return MONEYDJ_URL_TEMPLATE.format(code=etf_code.upper())


def fetch_html(url: str, timeout: int = 30) -> str:
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
        "Referer": "https://www.moneydj.com/ETF/",
    }
    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    # MoneyDJ returns Content-Type without charset; requests defaults to
    # ISO-8859-1 which garbles Chinese characters.  Force UTF-8.
    response.encoding = "utf-8"
    return response.text


def parse_date(html: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)

    data_date_match = re.search(
        r"(?:資料日期|日期)\s*[:：]?\s*(\d{4}/\d{2}/\d{2})",
        text,
    )
    if data_date_match:
        return data_date_match.group(1)

    date_match = re.search(r"\d{4}/\d{2}/\d{2}", text)
    return date_match.group(0) if date_match else None


def classify_asset(asset_name: str) -> dict:
    name = asset_name.strip()
    stock_match = re.fullmatch(r"(.+?)\((\d{4})\.TW\)", name, re.IGNORECASE)
    if stock_match:
        return {
            "asset_type": "stock",
            "stock_code": stock_match.group(2),
            "stock_name": stock_match.group(1).strip(),
        }

    lower_name = name.lower()
    if "cash" in lower_name or "現金" in name:
        asset_type = "cash"
    elif "futures" in lower_name or "期貨" in name:
        asset_type = "futures"
    elif "options" in lower_name or "選擇權" in name:
        asset_type = "options"
    elif "bond" in lower_name or "債" in name:
        asset_type = "bond"
    else:
        asset_type = "unknown"

    return {
        "asset_type": asset_type,
        "stock_code": None,
        "stock_name": None,
    }


def parse_moneydj_rows(etf_code: str, html: str, source_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    date = parse_date(html)
    rows = []

    for table in soup.select("table.datalist"):
        for tr in table.select("tbody tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cells) != 3:
                continue

            asset_name, weight_text, shares_text = cells
            weight_pct = _parse_float(weight_text)
            shares = _parse_number(shares_text)
            classification = classify_asset(asset_name)

            rows.append(
                {
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
                    "extraction_method": EXTRACTION_METHOD,
                }
            )

    return rows


def dedupe_rows(rows: list) -> list:
    seen = set()
    deduped = []

    for row in rows:
        key = (
            row.get("etf_code"),
            row.get("date"),
            row.get("asset_name"),
            row.get("weight_pct"),
            row.get("shares"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)

    return deduped


def validate_rows(rows: list) -> tuple[bool, str]:
    if not rows:
        return False, "empty rows"

    if len(rows) < 5:
        return False, "fewer than 5 rows"

    if any(not row.get("date") for row in rows):
        return False, "missing date"

    if any(row.get("weight_pct") is None for row in rows):
        return False, "missing weight_pct"

    stock_rows = [row for row in rows if row.get("asset_type") == "stock"]
    if len(stock_rows) < 5:
        return False, "fewer than 5 Taiwan stock rows"

    for row in stock_rows:
        stock_code = row.get("stock_code")
        stock_name = row.get("stock_name")
        if not stock_name or not re.fullmatch(r"\d{4}", str(stock_code or "")):
            return False, "invalid Taiwan stock row"

    return True, "ok"


def _weight_warning(total_weight: float) -> dict | None:
    if total_weight < WARNING_MIN_TOTAL_WEIGHT:
        reason = "total_weight_below_expected_range"
    elif total_weight > WARNING_MAX_TOTAL_WEIGHT:
        reason = "total_weight_above_expected_range"
    else:
        return None

    return {
        "reason": reason,
        "source_total_weight_all_rows": total_weight,
        "minimum_expected_weight": WARNING_MIN_TOTAL_WEIGHT,
        "maximum_expected_weight": WARNING_MAX_TOTAL_WEIGHT,
    }


def split_rows(rows: list) -> tuple[list, list]:
    stock_rows = [row for row in rows if row.get("asset_type") == "stock"]
    non_stock_rows = [row for row in rows if row.get("asset_type") != "stock"]
    return stock_rows, non_stock_rows


def scrape_moneydj(etf_code: str) -> dict:
    source_url = build_moneydj_url(etf_code)

    try:
        html = fetch_html(source_url)
        all_rows = dedupe_rows(parse_moneydj_rows(etf_code, html, source_url))
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

    result = {
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
    if ok:
        warning = _weight_warning(total_weight_all_rows)
        if warning is not None:
            result["weight_warning"] = warning
    return result


def _parse_float(value: str) -> float | None:
    cleaned = value.strip().replace(",", "").replace("%", "")
    if not cleaned or cleaned in {"-", "--"}:
        return None
    result = float(cleaned)
    if result == 0.0:
        result = 0.004
    return result


def _parse_number(value: str) -> int | float | None:
    cleaned = value.strip().replace(",", "")
    if not cleaned or cleaned.upper() in {"-", "--", "N/A", "NA"}:
        return None

    number = float(cleaned)
    return int(number) if number.is_integer() else number


def _sum_weights(rows: list) -> float:
    return round(sum(row["weight_pct"] for row in rows if row.get("weight_pct") is not None), 2)
