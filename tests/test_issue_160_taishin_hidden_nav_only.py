from unittest.mock import patch

import pytest

from scrapers.official import parse_taishin, scrape_official_static


TAISHIN_PAGE_URL = "https://www.tsit.com.tw/ETF/Home/ETFSeriesDetail/00987A"


def _visible_only_nav_date_html() -> str:
    rows = "".join(
        f"<tr><td>{code}</td><td>{name}</td><td>{shares}</td><td>{weight}</td></tr>"
        for code, name, shares, weight in [
            ("2330", "台積電", "620000", "30.1"),
            ("2881", "富邦金", "430000", "20.4"),
            ("3711", "日月光投控", "330000", "18.9"),
            ("2308", "台達電", "250000", "16.2"),
            ("2454", "聯發科", "120000", "14.4"),
        ]
    )
    return f"""
    <html>
      <body>
        <input name="NAV_DATE" value="2026/8/19">
        <table>
          <tr><th>股票代號</th><th>股票名稱</th><th>股數</th><th>權重</th></tr>
          {rows}
        </table>
      </body>
    </html>
    """


def test_taishin_parser_rejects_visible_nav_date_when_hidden_nav_date_is_missing():
    with pytest.raises(ValueError, match="^Taishin NAV_DATE is missing$"):
        parse_taishin(_visible_only_nav_date_html(), "00987A", TAISHIN_PAGE_URL)


def test_taishin_static_scraper_fails_closed_when_only_visible_nav_date_exists():
    config = {
        "url": TAISHIN_PAGE_URL,
        "method": "static",
        "issuer": "Taishin",
    }

    with patch("scrapers.official.get_official_config", return_value=config), patch(
        "scrapers.official.fetch_static",
        return_value=_visible_only_nav_date_html(),
    ):
        result = scrape_official_static("00987A")

    assert result["ok"] is False
    assert result["reason"] == "Taishin NAV_DATE is missing"
    assert result["all_rows"] == []
    assert result["source_url"] == TAISHIN_PAGE_URL
    assert result["source_type"] == "official_fallback"
