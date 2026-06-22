import json
import pytest
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
    scrape_capital_playwright,
    scrape_nomura_stealth,
    scrape_mega_playwright,
    scrape_uni_president_playwright,
    scrape_official_with_browser,
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

# Rich fixture for async tests — passes validate_rows (total weight >= 80%)
_CAPITAL_STOCKS = [
    {"stocNo": "2330", "stocName": "台積電", "share": 800000.0, "weight": 12.30, "weightRound": 12.30},
    {"stocNo": "2317", "stocName": "鴻海", "share": 500000.0, "weight": 8.20, "weightRound": 8.20},
    {"stocNo": "2382", "stocName": "廣達", "share": 210000.0, "weight": 6.10, "weightRound": 6.10},
    {"stocNo": "2308", "stocName": "台達電", "share": 300000.0, "weight": 5.50, "weightRound": 5.50},
    {"stocNo": "2454", "stocName": "聯發科", "share": 180000.0, "weight": 4.80, "weightRound": 4.80},
    {"stocNo": "2881", "stocName": "富邦金", "share": 400000.0, "weight": 4.50, "weightRound": 4.50},
    {"stocNo": "2882", "stocName": "國泰金", "share": 350000.0, "weight": 4.20, "weightRound": 4.20},
    {"stocNo": "2891", "stocName": "中信金", "share": 500000.0, "weight": 4.00, "weightRound": 4.00},
    {"stocNo": "3711", "stocName": "日月光投控", "share": 200000.0, "weight": 3.80, "weightRound": 3.80},
    {"stocNo": "2412", "stocName": "中華電", "share": 250000.0, "weight": 3.50, "weightRound": 3.50},
    {"stocNo": "3034", "stocName": "聯詠", "share": 150000.0, "weight": 3.20, "weightRound": 3.20},
    {"stocNo": "2395", "stocName": "研華", "share": 120000.0, "weight": 3.00, "weightRound": 3.00},
    {"stocNo": "3008", "stocName": "大立光", "share": 50000.0, "weight": 2.80, "weightRound": 2.80},
    {"stocNo": "2002", "stocName": "中鋼", "share": 600000.0, "weight": 2.60, "weightRound": 2.60},
    {"stocNo": "1301", "stocName": "台塑", "share": 300000.0, "weight": 2.50, "weightRound": 2.50},
    {"stocNo": "1303", "stocName": "南亞", "share": 280000.0, "weight": 2.40, "weightRound": 2.40},
    {"stocNo": "3045", "stocName": "台灣大", "share": 200000.0, "weight": 2.30, "weightRound": 2.30},
    {"stocNo": "6505", "stocName": "台塑化", "share": 250000.0, "weight": 2.20, "weightRound": 2.20},
    {"stocNo": "5880", "stocName": "合庫金", "share": 400000.0, "weight": 2.10, "weightRound": 2.10},
    {"stocNo": "5871", "stocName": "中租-KY", "share": 100000.0, "weight": 2.00, "weightRound": 2.00},
]
CAPITAL_API_JSON_RICH = json.dumps({
    "code": 200,
    "data": {
        "pcf": {"date2": "2026-06-18"},
        "stocks": _CAPITAL_STOCKS,
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

# Rich Nomura fixture for async tests — total weight >= 80%
_NOMURA_ROWS = [
    ["2330", "台灣積體電路製造", "704000", "9.58"],
    ["2308", "台達電子工業", "475000", "5.54"],
    ["2454", "聯發科技", "188000", "4.55"],
    ["2317", "鴻海精密工業", "500000", "5.20"],
    ["2382", "廣達電腦", "300000", "4.80"],
    ["2881", "富邦金融控股", "400000", "4.30"],
    ["2882", "國泰金融控股", "350000", "4.10"],
    ["2891", "中國信託金融控股", "500000", "3.90"],
    ["3711", "日月光投控", "200000", "3.50"],
    ["2412", "中華電信", "250000", "3.30"],
    ["3034", "聯詠科技", "150000", "3.10"],
    ["2395", "研華科技", "120000", "2.90"],
    ["3008", "大立光電", "50000", "2.70"],
    ["2002", "中國鋼鐵", "600000", "2.50"],
    ["1301", "台灣塑膠工業", "300000", "2.40"],
    ["1303", "南亞塑膠工業", "280000", "2.30"],
    ["3045", "台灣大哥大", "200000", "2.20"],
    ["6505", "台塑石化", "250000", "2.10"],
    ["5880", "合作金庫金融控股", "400000", "2.00"],
    ["5871", "中租-KY", "100000", "1.90"],
    ["2345", "智邦科技", "80000", "1.80"],
    ["6669", "緯穎科技", "60000", "1.70"],
    ["3661", "世芯-KY", "40000", "1.60"],
    ["2603", "長榮海運", "300000", "1.50"],
    ["2609", "陽明海運", "200000", "1.40"],
]
NOMURA_API_JSON_RICH = json.dumps({
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
                    "Rows": _NOMURA_ROWS,
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

# Rich Mega fixture for async tests — total weight >= 80%
MEGA_TEXT_RICH = """持股比重
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
12.31
2327
國巨
405,000
8.43
2454
聯發科
81,000
6.85
2317
鴻海
300,000
6.20
2382
廣達
200,000
5.50
2881
富邦金
350,000
4.80
2882
國泰金
300,000
4.30
2891
中信金
400,000
4.00
3711
日月光投控
180,000
3.50
2412
中華電
200,000
3.30
3034
聯詠
120,000
3.10
2395
研華
100,000
2.90
3008
大立光
40,000
2.70
2002
中鋼
500,000
2.50
1301
台塑
250,000
2.40
1303
南亞
230,000
2.30
3045
台灣大
160,000
2.20
6505
台塑化
200,000
2.10
5880
合庫金
320,000
2.00
5871
中租-KY
80,000
1.90
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


# ── Async browser scraper tests ──
# These test the Playwright-based scrapers using mock page objects.
# Each mock simulates: page.goto(), page.on('response'), page.wait_for_timeout(),
# page.remove_listener(), page.locator(), page.query_selector_all().


def _make_mock_response(url: str, body: str):
    """Create a mock Playwright Response with async .text()."""
    resp = AsyncMock()
    resp.url = url
    resp.text.return_value = body
    return resp


def _make_mock_page(responses=None, body_text=None, tables=None):
    """Create a mock Playwright Page.

    Args:
        responses: list of (url, body) tuples to fire as 'response' events.
        body_text: inner_text for <body> (Mega text parser).
        tables: list of table mock objects (Uni-President).
    """
    page = AsyncMock()
    page.goto = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.remove_listener = AsyncMock()

    # Capture the 'response' callback so we can fire it
    _callbacks = {}

    def _on(event, callback):
        _callbacks[event] = callback

    page.on = _on

    # After page is returned, caller can fire events via _fire_responses
    page._callbacks = _callbacks
    page._responses_to_fire = responses or []
    page._body_text = body_text
    page._tables = tables or []

    # Mock locator('body').inner_text() — locator() is SYNC in Playwright
    body_locator = AsyncMock()
    body_locator.inner_text.return_value = body_text or ""
    page.locator = Mock(return_value=body_locator)

    # Mock query_selector_all('table')
    page.query_selector_all = AsyncMock(return_value=tables or [])

    return page


async def _fire_response_events(page):
    """Fire queued response events through the page's callback."""
    callback = page._callbacks.get("response")
    if not callback:
        return
    for url, body in page._responses_to_fire:
        mock_resp = _make_mock_response(url, body)
        await callback(mock_resp)


# -- scrape_capital_playwright --

@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_scrape_capital_playwright_intercepts_api(mock_config):
    mock_config.return_value = {
        "url": CAPITAL_URL, "method": "api",
        "issuer": "Capital", "internal_id": "399", "official_logic": "buyback",
    }
    page = _make_mock_page(
        responses=[("https://www.capitalfund.com.tw/CFWeb/api/etf/buyback", CAPITAL_API_JSON_RICH)]
    )

    # Fire response events after goto is called
    async def goto_side_effect(*a, **kw):
        await _fire_response_events(page)

    page.goto.side_effect = goto_side_effect

    result = await scrape_capital_playwright("00982A", page)

    assert result["ok"] is True
    assert len(result["stock_rows"]) == 20
    assert result["stock_rows"][0]["stock_code"] == "2330"
    assert result["source_type"] == "official_fallback"


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_scrape_capital_playwright_no_api_intercepted(mock_config):
    mock_config.return_value = {
        "url": CAPITAL_URL, "method": "api",
        "issuer": "Capital", "internal_id": "399", "official_logic": "buyback",
    }
    page = _make_mock_page(responses=[])  # No responses fired

    result = await scrape_capital_playwright("00982A", page)

    assert result["ok"] is False
    assert "not intercepted" in result["reason"]


# -- scrape_nomura_stealth --

@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_scrape_nomura_stealth_intercepts_api(mock_config):
    mock_config.return_value = {
        "url": NOMURA_URL, "method": "stealth_api",
        "issuer": "Nomura", "internal_id": "00980A", "official_logic": "GetFundAssets",
    }
    page = _make_mock_page(
        responses=[("https://www.nomurafunds.com.tw/API/ETFAPI/api/Fund/GetFundAssets", NOMURA_API_JSON_RICH)]
    )

    async def goto_side_effect(*a, **kw):
        await _fire_response_events(page)

    page.goto.side_effect = goto_side_effect

    result = await scrape_nomura_stealth("00980A", page)

    assert result["ok"] is True
    assert len(result["stock_rows"]) == 25
    assert result["stock_rows"][0]["stock_code"] == "2330"
    assert result["stock_rows"][0]["stock_name"] == "台灣積體電路製造"


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_scrape_nomura_stealth_no_api_intercepted(mock_config):
    mock_config.return_value = {
        "url": NOMURA_URL, "method": "stealth_api",
        "issuer": "Nomura", "internal_id": "00980A", "official_logic": "GetFundAssets",
    }
    page = _make_mock_page(responses=[])

    result = await scrape_nomura_stealth("00980A", page)

    assert result["ok"] is False
    assert "not intercepted" in result["reason"]


# -- scrape_mega_playwright --

@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_scrape_mega_playwright_extracts_text(mock_config):
    mock_config.return_value = {
        "url": MEGA_URL, "method": "playwright",
        "issuer": "Mega", "internal_id": "23", "official_logic": "text",
    }
    page = _make_mock_page(body_text=MEGA_TEXT_RICH)

    result = await scrape_mega_playwright("00996A", page)

    assert result["ok"] is True
    assert len(result["stock_rows"]) == 20
    assert result["stock_rows"][0]["stock_code"] == "2330"
    assert result["stock_rows"][1]["stock_code"] == "2327"


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_scrape_mega_playwright_empty_page(mock_config):
    mock_config.return_value = {
        "url": MEGA_URL, "method": "playwright",
        "issuer": "Mega", "internal_id": "23", "official_logic": "text",
    }
    page = _make_mock_page(body_text="no holdings here")

    result = await scrape_mega_playwright("00996A", page)

    assert result["ok"] is False


# -- scrape_uni_president_playwright --

def _make_mock_table(rows_data):
    """Create a mock table with rows containing cells."""
    table = AsyncMock()
    mock_rows = []
    for i, row_cells in enumerate(rows_data):
        row = AsyncMock()
        if i == 0:
            # Header row — inner_text must be a string containing '股票'
            row.inner_text = AsyncMock(return_value=" ".join(row_cells))
            row.query_selector_all = AsyncMock(return_value=[])
        else:
            cells = []
            for cell_text in row_cells:
                cell = AsyncMock()
                cell.inner_text = AsyncMock(return_value=cell_text)
                cells.append(cell)
            row.inner_text = AsyncMock(return_value=" ".join(row_cells))
            row.query_selector_all = AsyncMock(return_value=cells)
        mock_rows.append(row)
    table.query_selector_all = AsyncMock(return_value=mock_rows)
    return table


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_scrape_uni_president_playwright_extracts_table(mock_config):
    mock_config.return_value = {
        "url": "https://www.ezmoney.com.tw/ETF/Fund/Info?fundCode=00403A",
        "method": "playwright",
        "issuer": "Uni-President", "internal_id": "00403A", "official_logic": "table",
    }

    # Build a table with 25 rows (passes the len(rows) >= 20 check)
    # Weights must sum to 80-150% to pass validation
    header = ["股票代號", "名稱", "持股數", "佔基金淨資產比例(%)"]
    weights = [
        "18.29", "8.50", "6.12", "5.80", "5.45", "5.10", "4.80", "4.50",
        "4.20", "3.90", "3.60", "3.30", "3.00", "2.70", "2.50", "2.30",
        "2.10", "1.90", "1.70", "1.50", "1.30", "1.10", "0.90", "0.70",
    ]
    data_rows = [["2330", "台積電", "13,300,000", w] for w in weights]
    rows_data = [header] + data_rows

    mock_table = _make_mock_table(rows_data)
    page = _make_mock_page(tables=[mock_table], body_text="2026/06/18 fund info page")

    result = await scrape_uni_president_playwright("00403A", page)

    assert result["ok"] is True
    assert len(result["stock_rows"]) == 24
    assert result["stock_rows"][0]["stock_code"] == "2330"
    assert result["stock_rows"][0]["date"] == "2026/06/18"


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_scrape_uni_president_playwright_no_table_found(mock_config):
    mock_config.return_value = {
        "url": "https://www.ezmoney.com.tw/ETF/Fund/Info?fundCode=00403A",
        "method": "playwright",
        "issuer": "Uni-President", "internal_id": "00403A", "official_logic": "table",
    }
    page = _make_mock_page(tables=[], body_text="no tables here")

    result = await scrape_uni_president_playwright("00403A", page)

    assert result["ok"] is False
    assert "not found" in result["reason"]


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_scrape_uni_president_skips_small_tables(mock_config):
    mock_config.return_value = {
        "url": "https://www.ezmoney.com.tw/ETF/Fund/Info?fundCode=00403A",
        "method": "playwright",
        "issuer": "Uni-President", "internal_id": "00403A", "official_logic": "table",
    }

    # Small table (only 5 rows) — should be skipped
    small_table = _make_mock_table([["header1", "header2"]] + [["a", "b"] for _ in range(4)])
    page = _make_mock_page(tables=[small_table], body_text="2026/06/18")

    result = await scrape_uni_president_playwright("00403A", page)

    assert result["ok"] is False


# -- scrape_official_with_browser (dispatcher) --

@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_scrape_official_with_browser_dispatches_capital(mock_config):
    mock_config.return_value = {
        "url": CAPITAL_URL, "method": "api",
        "issuer": "Capital", "internal_id": "399", "official_logic": "buyback",
    }
    page = _make_mock_page(
        responses=[("https://www.capitalfund.com.tw/CFWeb/api/etf/buyback", CAPITAL_API_JSON_RICH)]
    )

    async def goto_side_effect(*a, **kw):
        await _fire_response_events(page)

    page.goto.side_effect = goto_side_effect

    result = await scrape_official_with_browser("00982A", page)

    assert result["ok"] is True
    assert len(result["stock_rows"]) == 20


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_scrape_official_with_browser_dispatches_nomura(mock_config):
    mock_config.return_value = {
        "url": NOMURA_URL, "method": "stealth_api",
        "issuer": "Nomura", "internal_id": "00980A", "official_logic": "GetFundAssets",
    }
    page = _make_mock_page(
        responses=[("https://www.nomurafunds.com.tw/API/ETFAPI/api/Fund/GetFundAssets", NOMURA_API_JSON_RICH)]
    )

    async def goto_side_effect(*a, **kw):
        await _fire_response_events(page)

    page.goto.side_effect = goto_side_effect

    result = await scrape_official_with_browser("00980A", page)

    assert result["ok"] is True
    assert len(result["stock_rows"]) == 25


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_scrape_official_with_browser_dispatches_mega(mock_config):
    mock_config.return_value = {
        "url": MEGA_URL, "method": "playwright",
        "issuer": "Mega", "internal_id": "23", "official_logic": "text",
    }
    page = _make_mock_page(body_text=MEGA_TEXT_RICH)

    result = await scrape_official_with_browser("00996A", page)

    assert result["ok"] is True
    assert len(result["stock_rows"]) == 20


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_scrape_official_with_browser_dispatches_uni_president(mock_config):
    mock_config.return_value = {
        "url": "https://www.ezmoney.com.tw/ETF/Fund/Info?fundCode=00403A",
        "method": "playwright",
        "issuer": "Uni-President", "internal_id": "00403A", "official_logic": "table",
    }
    header = ["股票代號", "名稱", "持股數", "佔基金淨資產比例(%)"]
    weights = [
        "18.29", "8.50", "6.12", "5.80", "5.45", "5.10", "4.80", "4.50",
        "4.20", "3.90", "3.60", "3.30", "3.00", "2.70", "2.50", "2.30",
        "2.10", "1.90", "1.70", "1.50", "1.30", "1.10", "0.90", "0.70",
    ]
    data_rows = [["2330", "台積電", "13,300,000", w] for w in weights]
    mock_table = _make_mock_table([header] + data_rows)
    page = _make_mock_page(tables=[mock_table], body_text="2026/06/18")

    result = await scrape_official_with_browser("00403A", page)

    assert result["ok"] is True
    assert len(result["stock_rows"]) == 24


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config")
async def test_scrape_official_with_browser_unsupported_issuer(mock_config):
    mock_config.return_value = {
        "url": "https://example.com", "method": "unknown_method",
        "issuer": "Unknown", "internal_id": "X", "official_logic": "none",
    }
    page = _make_mock_page()

    result = await scrape_official_with_browser("9999A", page)

    assert result["ok"] is False
    assert "No browser official scraper" in result["reason"]
