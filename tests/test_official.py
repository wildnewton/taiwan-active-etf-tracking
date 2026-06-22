from unittest.mock import Mock, patch

from scrapers.official import (
    fetch_static,
    get_official_config,
    parse_capital,
    parse_fubon,
    parse_mega,
    parse_taishin,
    scrape_official_static,
)


FUBON_URL = "https://websys.fsit.com.tw/FubonETF/Fund/Assets.aspx?stkId=00405A"
CAPITAL_URL = "https://www.capitalfund.com.tw/etf/product/detail/399/buyback"
TAISHIN_URL = "https://www.tsit.com.tw/ETF/Home/ETFSeriesDetail/00987A"
MEGA_URL = "https://www.megafunds.com.tw/MEGA/etf/etf_product.aspx?id=23"
TWSE_00980A_URL = (
    "https://www.twse.com.tw/zh/products/securities/etf/products/content.html?00980A="
)


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


CAPITAL_HTML = """
<html>
  <body>
    <div>資料日期 2026/06/18</div>
    <table class="portfolio-table">
      <tr>
        <th>證券代號</th>
        <th>證券名稱</th>
        <th>持有股數</th>
        <th>投資比例</th>
      </tr>
      <tr><td>2330</td><td>台積電</td><td>800,000</td><td>12.30</td></tr>
      <tr><td>2317</td><td>鴻海</td><td>500,000</td><td>8.20</td></tr>
      <tr><td>2382</td><td>廣達</td><td>210,000</td><td>6.10</td></tr>
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


MEGA_HTML = """
<html>
  <body>
    <strong>資料日期：2026/06/18</strong>
    <table class="holdings">
      <thead>
        <tr>
          <th>代號</th>
          <th>股票名稱</th>
          <th>庫存股數</th>
          <th>權重</th>
        </tr>
      </thead>
      <tbody>
        <tr><td>2330</td><td>台積電</td><td>900,000</td><td>13.50%</td></tr>
        <tr><td>2412</td><td>中華電</td><td>300,000</td><td>4.25%</td></tr>
        <tr><td>2891</td><td>中信金</td><td>700,000</td><td>3.75%</td></tr>
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


def assert_stock_row(row, etf_code, stock_code, stock_name, shares, weight_pct):
    assert row["date"] == "2026/06/18"
    assert row["etf_code"] == etf_code
    assert row["asset_name"] == f"{stock_name}({stock_code}.TW)"
    assert row["asset_type"] == "stock"
    assert row["stock_code"] == stock_code
    assert row["stock_name"] == stock_name
    assert row["shares"] == shares
    assert row["weight_pct"] == weight_pct
    assert row["source_type"] == "official_fallback"
    assert row["extraction_method"] == "requests_bs4"


def test_get_official_config_returns_config():
    config = get_official_config("00405A")

    assert config["url"] == FUBON_URL
    assert config["method"] == "static"
    assert config["issuer"] == "Fubon"
    assert config["internal_id"] == "00405A"
    assert config["official_logic"] == "stkId=00405A"


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


def test_parse_fubon_rows():
    rows = parse_fubon(FUBON_HTML, "00405A", FUBON_URL)

    assert len(rows) == 3
    assert_stock_row(rows[0], "00405A", "2330", "台積電", 704000, 9.36)
    assert_stock_row(rows[1], "00405A", "2308", "台達電", 250000, 5.12)
    assert rows[0]["source_url"] == FUBON_URL


def test_parse_capital_rows():
    rows = parse_capital(CAPITAL_HTML, "00982A", CAPITAL_URL)

    assert len(rows) == 3
    assert_stock_row(rows[0], "00982A", "2330", "台積電", 800000, 12.30)
    assert_stock_row(rows[2], "00982A", "2382", "廣達", 210000, 6.10)
    assert rows[0]["source_url"] == CAPITAL_URL


def test_parse_taishin_rows():
    rows = parse_taishin(TAISHIN_HTML, "00987A", TAISHIN_URL)

    assert len(rows) == 3
    assert_stock_row(rows[0], "00987A", "2330", "台積電", 620000, 10.10)
    assert_stock_row(rows[2], "00987A", "3711", "日月光投控", 330000, 2.90)
    assert rows[0]["source_url"] == TAISHIN_URL


def test_parse_mega_rows():
    rows = parse_mega(MEGA_HTML, "00996A", MEGA_URL)

    assert len(rows) == 3
    assert_stock_row(rows[0], "00996A", "2330", "台積電", 900000, 13.50)
    assert_stock_row(rows[2], "00996A", "2891", "中信金", 700000, 3.75)
    assert rows[0]["source_url"] == MEGA_URL


def test_scrape_official_static_dispatches_to_correct_parser():
    response = Mock()
    response.text = CAPITAL_HTML
    response.raise_for_status.return_value = None

    with patch("scrapers.official.requests.get", return_value=response):
        result = scrape_official_static("00982A")

    assert result["source_url"] == CAPITAL_URL
    assert result["source_type"] == "official_fallback"
    assert len(result["all_rows"]) == 3
    assert result["all_rows"][0]["etf_code"] == "00982A"
    assert result["all_rows"][0]["stock_code"] == "2330"


def test_scrape_official_static_returns_valid_shape():
    response = Mock()
    response.text = VALID_FUBON_HTML
    response.raise_for_status.return_value = None

    with patch("scrapers.official.requests.get", return_value=response):
        result = scrape_official_static("00405A")

    assert result == {
        "ok": True,
        "reason": "ok",
        "all_rows": result["all_rows"],
        "stock_rows": result["stock_rows"],
        "non_stock_rows": [],
        "source_url": FUBON_URL,
        "source_type": "official_fallback",
        "total_weight_all_rows": 90.0,
        "total_weight_stock_rows": 90.0,
    }
    assert len(result["all_rows"]) == 5
    assert len(result["stock_rows"]) == 5


def test_scrape_official_static_uses_twse_for_non_static_issuers():
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
