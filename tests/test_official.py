import json
from unittest.mock import AsyncMock, Mock, patch

from scrapers.official import (
    fetch_static,
    get_official_config,
    parse_capital_api,
    parse_fubon,
    parse_mega_text,
    parse_nomura_api,
    parse_taishin,
    parse_uni_president_table,
    scrape_official_static,
)


FUBON_URL = "https://websys.fsit.com.tw/FubonETF/Fund/Assets.aspx?stkId=00405A"
CAPITAL_URL = "https://www.capitalfund.com.tw/etf/product/detail/399/buyback"
TAISHIN_URL = "https://www.tsit.com.tw/ETF/Home/ETFSeriesDetail/00987A"
MEGA_URL = "https://www.megafunds.com.tw/MEGA/etf/etf_product.aspx?id=23"
NOMURA_URL = (
    "https://www.nomurafunds.com.tw/ETFWEB/product-description"
    "?fundNo=00980A&tab=Shareholding"
)
TWSE_00980A_URL = (
    "https://www.twse.com.tw/zh/products/securities/etf/products/content.html?00980A="
)


# ── Static HTML fixtures (Fubon, Taishin) ──

FUBON_HTML = """
<html>
  <body>
    <span>資料日期：2026/06/18</span>
    <table id="AssetsGrid">
      <thead>
        <tr>
          <th>股票代號</th>
          <th>股票名稱</th>
          <th>股數</th>
          <th>權重(%)</th>
        </tr>
      </thead>
      <tbody>
        <tr><td>2330</td><td>台積電</td><td>704,000</td><td>9.36%</td></tr>
        <tr><td>2308</td><td>台達電</td><td>250,000</td><td>5.12%</td></tr>
        <tr><td>2454</td><td>聯發科</td><td>120,000</td><td>4.88%</td></tr>
      </tbody>
    </table>
  </body>
</html>
"""


TAISHIN_HTML = """
<html>
  <body>
    <p>日期：2026/06/18</p>
    <table>
      <thead>
        <tr>
          <th>股票代碼</th>
          <th>名稱</th>
          <th>持股數</th>
          <th>佔基金淨資產比例(%)</th>
        </tr>
      </thead>
      <tbody>
        <tr><td>2330</td><td>台積電</td><td>620,000</td><td>10.10%</td></tr>
        <tr><td>2881</td><td>富邦金</td><td>430,000</td><td>3.40%</td></tr>
        <tr><td>3711</td><td>日月光投控</td><td>330,000</td><td>2.90%</td></tr>
      </tbody>
    </table>
  </body>
</html>
"""


VALID_FUBON_HTML = """
<html>
  <body>
    <span>資料日期：2026/06/18</span>
    <table id="AssetsGrid">
      <thead>
        <tr><th>股票代號</th><th>股票名稱</th><th>股數</th><th>權重(%)</th></tr>
      </thead>
      <tbody>
        <tr><td>2330</td><td>台積電</td><td>100,000</td><td>20%</td></tr>
        <tr><td>2308</td><td>台達電</td><td>100,000</td><td>20%</td></tr>
        <tr><td>2454</td><td>聯發科</td><td>100,000</td><td>20%</td></tr>
        <tr><td>2317</td><td>鴻海</td><td>100,000</td><td>20%</td></tr>
        <tr><td>2382</td><td>廣達</td><td>100,000</td><td>10%</td></tr>
      </tbody>
    </table>
  </body>
</html>
"""


TWSE_HTML = """
<html>
  <body>
    <span>資料日期：2026/06/18</span>
    <table>
      <thead>
        <tr><th>證券代號</th><th>證券名稱</th><th>持有股數</th><th>權重</th></tr>
      </thead>
      <tbody>
        <tr><td>2330</td><td>台積電</td><td>100,000</td><td>20%</td></tr>
        <tr><td>2308</td><td>台達電</td><td>100,000</td><td>20%</td></tr>
        <tr><td>2454</td><td>聯發科</td><td>100,000</td><td>20%</td></tr>
        <tr><td>2317</td><td>鴻海</td><td>100,000</td><td>20%</td></tr>
        <tr><td>2382</td><td>廣達</td><td>100,000</td><td>10%</td></tr>
      </tbody>
    </table>
  </body>
</html>
"""


# ── API JSON fixtures (Capital, Nomura) ──

CAPITAL_API_JSON = json.dumps({
    "code": 200,
    "data": {
        "pcf": {"date2": "2026-06-18"},
        "stocks": [
            {"stocNo": "2330", "stocName": "台積電", "share": 800000.0, "weight": 12.3, "weightRound": 12.30},
            {"stocNo": "2317", "stocName": "鴻海", "share": 500000.0, "weight": 8.2, "weightRound": 8.20},
            {"stocNo": "2382", "stocName": "廣達", "share": 210000.0, "weight": 6.1, "weightRound": 6.10},
        ],
    },
})


NOMURA_API_JSON = json.dumps({
    "TotalPages": -1,
    "TotalItems": 0,
    "Entries": {
        "FundID": "00980A",
        "Data": {
            "FundAsset": {"Aum": "18450205183", "Nav": "25.72", "NavDate": "2026-06-22"},
            "Table": [
                {
                    "TableTitle": "股票",
                    "Columns": [
                        {"Name": "股票代號", "TextAlign": "center"},
                        {"Name": "股票名稱", "TextAlign": "center"},
                        {"Name": "股數", "TextAlign": "center"},
                        {"Name": "權重(%)", "TextAlign": "center"},
                    ],
                    "Rows": [
                        ["2330", "台灣積體電路製造", "704000", "9.58"],
                        ["2308", "台達電子工業", "475000", "5.54"],
                        ["2454", "聯發科技", "188000", "4.55"],
                    ],
                },
                {"TableTitle": "期貨", "Columns": [], "Rows": []},
            ],
        },
    },
})

# Mega text fixture — mimics Playwright inner_text output
MEGA_TEXT = """
持股比重
資料來源：兆豐投信，2026/06/18
基金資產
淨資產價值
項目 金額
股票 ( 96.46% )
TWD$ 5,005,569,200
現金/存款
2330
台積電
179,000
8.31
2327
國巨
405,000
8.43
2454
聯發科
81,000
6.85
"""


# ── Test helpers ──

def assert_stock_row(row, etf_code, stock_code, stock_name, shares, weight_pct):
    assert row["etf_code"] == etf_code
    assert row["asset_name"] == f"{stock_name}({stock_code}.TW)"
    assert row["asset_type"] == "stock"
    assert row["stock_code"] == stock_code
    assert row["stock_name"] == stock_name
    assert row["shares"] == shares
    assert row["weight_pct"] == weight_pct
    assert row["source_type"] == "official_fallback"


# ── Config tests ──

def test_get_official_config_returns_config():
    config = get_official_config("00405A")

    assert config["url"] == FUBON_URL
    assert config["method"] == "static"
    assert config["issuer"] == "Fubon"
    assert config["internal_id"] == "00405A"
    assert config["official_logic"] == "stkId=00405A"


def test_get_official_config_capital_is_api():
    config = get_official_config("00982A")

    assert config["method"] == "api"
    assert config["issuer"] == "Capital"
    assert "buyback" in config["url"]


def test_get_official_config_nomura_is_stealth_api():
    config = get_official_config("00980A")

    assert config["method"] == "stealth_api"
    assert config["issuer"] == "Nomura"


# ── Fetch tests ──

def test_fetch_static_uses_browser_headers():
    response = Mock()
    response.text = "<html></html>"
    response.raise_for_status.return_value = None

    with patch("scrapers.official.requests.get", return_value=response) as mock_get:
        html = fetch_static(FUBON_URL)

    assert html == "<html></html>"
    _, kwargs = mock_get.call_args
    assert kwargs["timeout"] == 30
    assert "Mozilla/5.0" in kwargs["headers"]["User-Agent"]
    assert "zh-TW" in kwargs["headers"]["Accept-Language"]
    response.raise_for_status.assert_called_once()


# ── Static parser tests (Fubon, Taishin) ──

def test_parse_fubon_rows():
    rows = parse_fubon(FUBON_HTML, "00405A", FUBON_URL)

    assert len(rows) == 3
    assert_stock_row(rows[0], "00405A", "2330", "台積電", 704000, 9.36)
    assert_stock_row(rows[1], "00405A", "2308", "台達電", 250000, 5.12)
    assert rows[0]["source_url"] == FUBON_URL
    assert rows[0]["extraction_method"] == "requests_bs4"


def test_parse_taishin_rows():
    rows = parse_taishin(TAISHIN_HTML, "00987A", TAISHIN_URL)

    assert len(rows) == 3
    assert_stock_row(rows[0], "00987A", "2330", "台積電", 620000, 10.10)
    assert_stock_row(rows[2], "00987A", "3711", "日月光投控", 330000, 2.90)
    assert rows[0]["source_url"] == TAISHIN_URL


# ── API parser tests (Capital, Nomura) ──

def test_parse_capital_api_rows():
    rows = parse_capital_api(CAPITAL_API_JSON, "00982A", CAPITAL_URL)

    assert len(rows) == 3
    assert rows[0]["stock_code"] == "2330"
    assert rows[0]["stock_name"] == "台積電"
    assert rows[0]["shares"] == 800000
    assert rows[0]["weight_pct"] == 12.30
    assert rows[0]["date"] == "2026/06/18"
    assert rows[0]["extraction_method"] == "playwright_api_intercept"
    assert rows[0]["source_type"] == "official_fallback"


def test_parse_nomura_api_rows():
    rows = parse_nomura_api(NOMURA_API_JSON, "00980A", NOMURA_URL)

    assert len(rows) == 3
    assert rows[0]["stock_code"] == "2330"
    assert rows[0]["stock_name"] == "台灣積體電路製造"
    assert rows[0]["shares"] == 704000
    assert rows[0]["weight_pct"] == 9.58
    assert rows[0]["date"] == "2026/06/22"
    assert rows[0]["extraction_method"] == "stealth_playwright_api"


def test_parse_nomura_api_skips_non_stock_tables():
    rows = parse_nomura_api(NOMURA_API_JSON, "00980A", NOMURA_URL)

    # Only "股票" table should be parsed, not "期貨"
    asset_types = {r["asset_type"] for r in rows}
    assert asset_types == {"stock"}


# ── Playwright text parser tests (Mega) ──

def test_parse_mega_text_rows():
    rows = parse_mega_text(MEGA_TEXT, "00996A", MEGA_URL)

    assert len(rows) == 3
    assert rows[0]["stock_code"] == "2330"
    assert rows[0]["stock_name"] == "台積電"
    assert rows[0]["shares"] == 179000
    assert rows[0]["weight_pct"] == 8.31
    assert rows[0]["extraction_method"] == "playwright_table_parse"


# ── Playwright table parser tests (Uni-President) ──

def test_parse_uni_president_table_rows():
    table_data = [
        ["2330", "台積電", "13,300,000", "18.29%"],
        ["2327", "國巨", "10,050,000", "6.19%"],
        ["2303", "聯電", "72,400,000", "6.01%"],
    ]
    rows = parse_uni_president_table(table_data, "00403A", "https://example.com", "2026/06/18")

    assert len(rows) == 3
    assert rows[0]["stock_code"] == "2330"
    assert rows[0]["stock_name"] == "台積電"
    assert rows[0]["shares"] == 13300000
    assert rows[0]["weight_pct"] == 18.29
    assert rows[0]["date"] == "2026/06/18"
    assert rows[0]["extraction_method"] == "playwright_table_parse"


def test_parse_uni_president_skips_non_stock_rows():
    table_data = [
        ["2330", "台積電", "13,300,000", "18.29%"],
        ["期貨", "台指期", "N/A", "5.00%"],  # Not a 4-digit stock code
    ]
    rows = parse_uni_president_table(table_data, "00403A", "https://example.com")

    assert len(rows) == 1
    assert rows[0]["stock_code"] == "2330"


# ── Integration tests ──

def test_scrape_official_static_fubon():
    response = Mock()
    response.text = VALID_FUBON_HTML
    response.raise_for_status.return_value = None

    with patch("scrapers.official.requests.get", return_value=response):
        result = scrape_official_static("00405A")

    assert result["ok"] is True
    assert result["source_url"] == FUBON_URL
    assert result["source_type"] == "official_fallback"
    assert len(result["all_rows"]) == 5
    assert result["total_weight_all_rows"] == 90.0


def test_scrape_official_static_falls_back_to_twse():
    response = Mock()
    response.text = TWSE_HTML
    response.raise_for_status.return_value = None

    with patch("scrapers.official.requests.get", return_value=response) as mock_get:
        result = scrape_official_static("00980A")

    assert result["ok"] is True
    assert result["source_url"] == TWSE_00980A_URL
    assert result["source_type"] == "official_fallback"
    assert len(result["stock_rows"]) == 5
    mock_get.assert_called_once()
