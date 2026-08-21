import json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, Mock, call, patch

import pytest

import scraper
import scrapers.official as official
from scrapers.official import (
    _is_ctbc_holdings_response,
    parse_fubon,
    parse_taishin,
    scrape_ctbc_playwright,
    scrape_mega_playwright,
    scrape_official_static,
    scrape_official_with_browser,
)


CTBC_API_URL = "https://www.ctbcinvestments.com.tw/API/etf/ETFHoldingWeight"
CTBC_PAGE_URL = "https://www.ctbcinvestments.com/Etf/00682450/Combination"
TAISHIN_PAGE_URL = "https://www.tsit.com.tw/ETF/Home/ETFSeriesDetail/00987A"
MEGA_PAGE_URL = "https://www.megafunds.com.tw/MEGA/etf/etf_product.aspx?id=23"
TARGET_DATE = date(2026, 8, 20)
TARGET_DATE_TEXT = "2026/08/20"
FIXTURES = Path(__file__).parent / "fixtures"
TAISHIN_NAV_INPUT = (
    '<input type="hidden" name="NAV_DATE" value="2026/8/20 上午 12:00:00">'
)
TAISHIN_INVALID_ROW = (
    "<tr><td>0000</td><td>非持股項目</td><td>0</td><td>－</td></tr>"
)


_STOCKS = [
    ("2330", "台積電", 620_000, 30.1),
    ("2881", "富邦金", 430_000, 20.4),
    ("3711", "日月光投控", 330_000, 18.9),
    ("2308", "台達電", 250_000, 16.2),
    ("2454", "聯發科", 120_000, 14.4),
]


class _Request:
    def __init__(self, method):
        self.method = method


class _Response:
    def __init__(self, url, body="{}", *, method="POST", ok=True):
        self.url = url
        self.ok = ok
        self.request = _Request(method)
        self._body = body

    async def text(self):
        return self._body


class _ResponseInfo:
    def __init__(self, response):
        self._response = response

    @property
    def value(self):
        async def resolve():
            return self._response

        return resolve()


class _ResponseWait:
    def __init__(self, page, predicate):
        self._page = page
        self._predicate = predicate

    async def __aenter__(self):
        self._page.order.append("response_wait_registered")
        assert self._predicate(self._page.response) is True
        return _ResponseInfo(self._page.response)

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _CtbcPage:
    def __init__(self, response):
        self.response = response
        self.order = []

    def expect_response(self, predicate, timeout):
        assert timeout <= 10_000
        return _ResponseWait(self, predicate)

    async def goto(self, url, **kwargs):
        self.order.append("goto")


def _ctbc_payload():
    return json.dumps(
        {
            "Data": {
                "FundAssets": [{"資料日期": TARGET_DATE_TEXT}],
                "FundAssetsDetail": [
                    {
                        "Code": "STOCK",
                        "Data": [
                            {
                                "code_": code,
                                "name_": name,
                                "qty_": str(shares),
                                "weights_": str(weight),
                            }
                            for code, name, shares, weight in _STOCKS
                        ],
                    }
                ],
            }
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("etf_code", ["00406A", "00995A"])
async def test_ctbc_handler_accepts_official_post_response_before_navigation(etf_code):
    page = _CtbcPage(_Response(CTBC_API_URL, _ctbc_payload(), method="POST"))
    config = {"url": CTBC_PAGE_URL, "method": "browser", "issuer": "CTBC"}

    with patch("scrapers.official.get_official_config", return_value=config):
        result = await scrape_ctbc_playwright(etf_code, page)

    assert result["ok"] is True
    assert {row["etf_code"] for row in result["all_rows"]} == {etf_code}
    assert {row["date"] for row in result["all_rows"]} == {TARGET_DATE_TEXT}
    assert page.order == ["response_wait_registered", "goto"]


@pytest.mark.parametrize(
    "response",
    [
        pytest.param(
            _Response(
                "https://evilctbcinvestments.com.tw/API/etf/ETFHoldingWeight",
                method="POST",
            ),
            id="wrong-domain",
        ),
        pytest.param(
            _Response(
                "https://www.ctbcinvestments.com.tw/API/etf/OtherEndpoint",
                method="POST",
            ),
            id="wrong-path",
        ),
        pytest.param(_Response(CTBC_API_URL, method="GET"), id="get"),
        pytest.param(_Response(CTBC_API_URL, method="PUT"), id="other-method"),
        pytest.param(_Response(CTBC_API_URL, method="POST", ok=False), id="non-success"),
    ],
)
def test_ctbc_response_predicate_fails_closed(response):
    assert _is_ctbc_holdings_response(response) is False


def _taishin_html(*, nav_date="2026/8/20 上午 12:00:00", include_dash_row=True):
    html = (FIXTURES / "taishin_fullwidth_dash.html").read_text(encoding="utf-8")
    if nav_date is None:
        html = html.replace(TAISHIN_NAV_INPUT, "")
    else:
        html = html.replace(
            TAISHIN_NAV_INPUT,
            f'<input type="hidden" name="NAV_DATE" value="{nav_date}">',
        )
    if not include_dash_row:
        html = html.replace(TAISHIN_INVALID_ROW, "")
    return html


def test_taishin_parser_uses_normalized_nav_date_and_skips_fullwidth_dash_row():
    html = _taishin_html()

    rows = parse_taishin(html, "00987A", TAISHIN_PAGE_URL)

    assert rows == [
        {
            "date": TARGET_DATE_TEXT,
            "etf_code": "00987A",
            "asset_name": f"{name}({code}.TW)",
            "asset_type": "stock",
            "stock_code": code,
            "stock_name": name,
            "shares": shares,
            "weight_pct": weight,
            "source_url": TAISHIN_PAGE_URL,
            "source_type": "official_fallback",
            "extraction_method": "requests_bs4",
        }
        for code, name, shares, weight in _STOCKS
    ]


@pytest.mark.parametrize(
    ("nav_date", "expected_message"),
    [
        (None, "Taishin NAV_DATE is missing"),
        ("not-a-date", "Taishin NAV_DATE is invalid"),
        ("2026/2/30", "Taishin NAV_DATE is invalid"),
    ],
    ids=["missing", "malformed", "calendar-invalid"],
)
def test_taishin_parser_rejects_missing_or_invalid_nav_date(
    nav_date,
    expected_message,
):
    html = _taishin_html(nav_date=nav_date, include_dash_row=False)

    with pytest.raises(ValueError, match=f"^{expected_message}$"):
        parse_taishin(html, "00987A", TAISHIN_PAGE_URL)


@pytest.mark.parametrize(
    ("nav_date", "expected_reason"),
    [
        (None, "Taishin NAV_DATE is missing"),
        ("not-a-date", "Taishin NAV_DATE is invalid"),
        ("2026/2/30", "Taishin NAV_DATE is invalid"),
    ],
    ids=["missing", "malformed", "calendar-invalid"],
)
def test_taishin_static_scraper_returns_clean_failure_for_invalid_nav_date(
    nav_date,
    expected_reason,
):
    html = _taishin_html(nav_date=nav_date, include_dash_row=False)
    config = {
        "url": TAISHIN_PAGE_URL,
        "method": "static",
        "issuer": "Taishin",
    }

    with patch("scrapers.official.get_official_config", return_value=config), patch(
        "scrapers.official.fetch_static",
        return_value=html,
    ):
        result = scrape_official_static("00987A")

    assert result == {
        "ok": False,
        "reason": expected_reason,
        "all_rows": [],
        "stock_rows": [],
        "non_stock_rows": [],
        "source_url": TAISHIN_PAGE_URL,
        "source_type": "official_fallback",
        "total_weight_all_rows": 0.0,
        "total_weight_stock_rows": 0.0,
    }


def test_shared_official_table_parser_preserves_fubon_numeric_rows():
    html = """
    <p>資料日期：2026/08/20</p>
    <table>
      <tr><th>股票代號</th><th>股票名稱</th><th>股數</th><th>權重</th></tr>
      <tr><td>2330</td><td>台積電</td><td>620,000</td><td>30.10%</td></tr>
    </table>
    """
    source_url = "https://websys.fsit.com.tw/FubonETF/Fund/Assets.aspx?stkId=00405A"

    rows = parse_fubon(html, "00405A", source_url)

    assert rows == [
        {
            "date": TARGET_DATE_TEXT,
            "etf_code": "00405A",
            "asset_name": "台積電(2330.TW)",
            "asset_type": "stock",
            "stock_code": "2330",
            "stock_name": "台積電",
            "shares": 620_000,
            "weight_pct": 30.1,
            "source_url": source_url,
            "source_type": "official_fallback",
            "extraction_method": "requests_bs4",
        }
    ]


def _mega_body(*, named_date=TARGET_DATE_TEXT):
    lines = ["網站一般更新日期：2026/08/19", "持股比重"]
    if named_date is not None:
        lines.append(f"資料來源：兆豐投信，{named_date}")
    for code, name, shares, weight in _STOCKS:
        lines.extend([code, name, f"{shares:,}", str(weight)])
    return "\n".join(lines)


def _mega_page(body_text):
    page = AsyncMock()
    page.goto = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    body = Mock()
    body.inner_text = AsyncMock(return_value=body_text)
    page.locator = Mock(return_value=body)
    return page


def _mega_config():
    return {"url": MEGA_PAGE_URL, "method": "playwright", "issuer": "Mega"}


@pytest.mark.asyncio
async def test_mega_uses_named_holdings_date_instead_of_earlier_generic_date():
    page = _mega_page(_mega_body())

    with patch("scrapers.official.get_official_config", return_value=_mega_config()):
        result = await scrape_mega_playwright("00996A", page, target_date=TARGET_DATE)

    assert result["ok"] is True
    assert result["reason"] == "ok"
    assert result["source_url"] == MEGA_PAGE_URL
    assert result["source_type"] == "official_fallback"
    assert len(result["all_rows"]) == 5
    assert {row["date"] for row in result["all_rows"]} == {TARGET_DATE_TEXT}
    assert {row["etf_code"] for row in result["all_rows"]} == {"00996A"}
    assert {row["source_url"] for row in result["all_rows"]} == {MEGA_PAGE_URL}
    assert {row["source_type"] for row in result["all_rows"]} == {"official_fallback"}
    assert {row["extraction_method"] for row in result["all_rows"]} == {
        "playwright_table_parse"
    }


@pytest.mark.asyncio
async def test_mega_missing_named_holdings_date_returns_clean_failure():
    page = _mega_page(_mega_body(named_date=None))

    with patch("scrapers.official.get_official_config", return_value=_mega_config()):
        result = await scrape_mega_playwright("00996A", page, target_date=TARGET_DATE)

    assert result["ok"] is False
    assert "date" in result["reason"].lower()
    assert result["all_rows"] == []
    assert result["source_url"] == MEGA_PAGE_URL
    assert result["source_type"] == "official_fallback"


@pytest.mark.asyncio
async def test_mega_stale_named_holdings_date_returns_clean_failure():
    page = _mega_page(_mega_body(named_date="2026/08/19"))

    with patch("scrapers.official.get_official_config", return_value=_mega_config()):
        result = await scrape_mega_playwright("00996A", page, target_date=TARGET_DATE)

    assert result["ok"] is False
    assert "date" in result["reason"].lower()
    assert result["all_rows"] == []
    assert result["source_url"] == MEGA_PAGE_URL
    assert result["source_type"] == "official_fallback"


@pytest.mark.asyncio
async def test_mega_preserves_snapshot_validation_failure():
    page = _mega_page(_mega_body())

    with patch(
        "scrapers.official.get_official_config",
        return_value=_mega_config(),
    ), patch(
        "scrapers.official.validate_snapshot_rows",
        return_value=(False, "validation sentinel"),
    ) as validator:
        result = await scrape_mega_playwright("00996A", page, target_date=TARGET_DATE)

    assert result["ok"] is False
    assert result["reason"] == "validation sentinel"
    assert len(result["all_rows"]) == 5
    validator.assert_called_once_with(result["all_rows"])


@pytest.mark.asyncio
async def test_mega_dispatcher_passes_target_date_to_handler():
    page = object()
    handler_result = {"ok": False, "reason": "sentinel"}
    handler = AsyncMock(return_value=handler_result)

    with patch("scrapers.official.get_official_config", return_value=_mega_config()), patch.object(
        official,
        "scrape_mega_playwright",
        new=handler,
    ):
        result = await scrape_official_with_browser(
            "00996A",
            page,
            target_date=TARGET_DATE,
        )

    assert result is handler_result
    handler.assert_awaited_once_with("00996A", page, target_date=TARGET_DATE)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("etf_code", "official_method", "issuer", "expected_kwargs"),
    [
        ("00401A", "api", "JPMorgan", {"target_date": TARGET_DATE}),
        ("00984A", "playwright", "Allianz", {"target_date": TARGET_DATE}),
        ("00996A", "playwright", "Mega", {"target_date": TARGET_DATE}),
        ("00406A", "browser", "CTBC", {}),
        ("00980A", "stealth_api", "Nomura", {}),
        ("00403A", "playwright", "Uni-President", {}),
    ],
)
async def test_pipeline_passes_target_date_only_to_date_bound_browser_issuers(
    etf_code,
    official_method,
    issuer,
    expected_kwargs,
):
    page = object()
    dispatcher = AsyncMock(return_value={"ok": True})

    with patch(
        "scraper.get_etf_config",
        return_value={"official_method": official_method, "issuer": issuer},
    ), patch(
        "scraper.scrape_official_with_browser",
        new=dispatcher,
    ), patch(
        "scraper._normalize_source_result",
        side_effect=lambda result, source_type: result,
    ):
        result = await scraper._official_fallback_with_browser(
            etf_code,
            page,
            target_date=TARGET_DATE,
        )

    assert result["ok"] is True
    dispatcher.assert_awaited_once_with(etf_code, page, **expected_kwargs)


@pytest.mark.asyncio
async def test_mega_browser_failure_falls_through_to_static_after_dated_dispatch():
    page = object()
    browser_result = {"ok": False, "reason": "stale holdings date"}
    static_result = {"ok": False, "reason": "static sentinel"}
    manager = Mock()
    dispatcher = AsyncMock(return_value=browser_result)
    static_fallback = Mock(return_value=static_result)
    manager.attach_mock(dispatcher, "browser")
    manager.attach_mock(static_fallback, "static")

    with patch(
        "scraper.get_etf_config",
        return_value={"official_method": "playwright", "issuer": "Mega"},
    ), patch(
        "scraper.scrape_official_with_browser",
        new=dispatcher,
    ), patch(
        "scraper._official_fallback_static",
        new=static_fallback,
    ):
        result = await scraper._official_fallback_with_browser(
            "00996A",
            page,
            target_date=TARGET_DATE,
        )

    assert result is static_result
    assert manager.mock_calls == [
        call.browser("00996A", page, target_date=TARGET_DATE),
        call.static("00996A"),
    ]
