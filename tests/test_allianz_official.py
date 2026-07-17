import json
from unittest.mock import AsyncMock, Mock, call, patch

import pytest

import scrapers.official as official


ALLIANZ_URL = "https://etf.allianzgi.com.tw/list-trade"
OPTIONS_URL = (
    "https://etf.allianzgi.com.tw/webapi/api/Category/GetFundDropdownOptions"
)
TRADE_URL = "https://etf.allianzgi.com.tw/webapi/api/Fund/GetFundTradeInfo"
COMBOBOX_SELECTOR = '[role="combobox"][aria-label*="主動安聯"]'


OPTIONS_JSON = json.dumps(
    {
        "Entries": [
            {
                "FundNo": "E0001",
                "SecuritiesCode": "00984A",
                "FundName": "主動安聯台灣高息",
            },
            {
                "FundNo": "E0002",
                "SecuritiesCode": "00993A",
                "FundName": "主動安聯台灣",
            },
            {
                "FundNo": "E0003",
                "SecuritiesCode": "00402A",
                "FundName": "主動安聯全球非投等債",
            },
        ],
        "StatusCode": 0,
    }
)


def _trade_payload(
    *,
    etf_code="00993A",
    fund_no="E0002",
    pcf_date="2026-07-17T00:00:00",
    include_stock_table=True,
):
    tables = []
    if include_stock_table:
        tables.append(
            {
                "TableTitle": "股票 (96.67%)",
                "Columns": [
                    {"Name": "序號", "TextAlign": "center"},
                    {"Name": "股票代號", "TextAlign": "center"},
                    {"Name": "股票名稱", "TextAlign": "center"},
                    {"Name": "股數", "TextAlign": "center"},
                    {"Name": "權重(%)", "TextAlign": "center"},
                ],
                "Rows": [
                    ["1", "2330", "台積電", "475,000", "11.51%"],
                    ["2", "2454", "聯發科", "164,000", "5.95%"],
                    ["3", "2383", "台光電子", "110,000", "5.38%"],
                    ["4", "6223", "旺矽", "83,000", "5.06%"],
                    ["5", "2327", "國巨*", "592,000", "4.80%"],
                ],
            }
        )
    tables.append(
        {
            "TableTitle": "期貨",
            "Columns": [
                {"Name": "序號"},
                {"Name": "期貨代號"},
                {"Name": "期貨名稱"},
                {"Name": "口數"},
                {"Name": "權重(%)"},
                {"Name": "契約年月"},
            ],
            "Rows": [["1", "TX", "台指期貨", "19", "1.71%", "2026/08"]],
        }
    )
    return {
        "Entries": {
            "CFundId": fund_no,
            "CSecuritiesCode": etf_code,
            "CPcfdate": pcf_date,
            "CNavDt": "2026-07-16T00:00:00",
            "CBeneficiariesCountDate": "2026-06-30T00:00:00",
            "DynamicTableData": tables,
        },
        "StatusCode": 0,
    }


class _FakeResponse:
    def __init__(
        self,
        url,
        payload,
        *,
        method="POST",
        ok=True,
        status=200,
    ):
        self.url = url
        self._body = json.dumps(payload)
        self.request = Mock(method=method)
        self.ok = ok
        self.status = status

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


class _ExpectResponseContext:
    def __init__(self, response, predicate):
        self._response = response
        self._predicate = predicate

    async def __aenter__(self):
        assert self._predicate(self._response), self._response.url
        return _ResponseInfo(self._response)

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _FakeLocator:
    def __init__(self, count=1):
        self.count = AsyncMock(return_value=count)
        self.click = AsyncMock()
        self.wait_for = AsyncMock()


def _mock_page(
    responses,
    *,
    option_code="00993A",
    option_count=1,
    combobox_count=1,
):
    queued = list(responses)
    page = Mock()
    page.goto = AsyncMock()
    page.request = Mock()

    def expect_response(predicate, timeout):
        assert timeout == 10_000
        assert queued, "unexpected expect_response call"
        return _ExpectResponseContext(queued.pop(0), predicate)

    page.expect_response = Mock(side_effect=expect_response)
    combobox = _FakeLocator(combobox_count)
    option = _FakeLocator(option_count)
    option_selector = f'[role="option"][aria-label^="{option_code} "]'

    def locator(selector):
        if selector == COMBOBOX_SELECTOR:
            return combobox
        if selector == option_selector:
            return option
        raise AssertionError(f"unexpected locator: {selector}")

    page.locator = Mock(side_effect=locator)
    return page, combobox, option, queued


def _options_response(*, ok=True, status=200, payload=None):
    return _FakeResponse(
        OPTIONS_URL,
        payload or json.loads(OPTIONS_JSON),
        ok=ok,
        status=status,
    )


def _trade_response(*, ok=True, status=200, **payload_kwargs):
    return _FakeResponse(
        TRADE_URL,
        _trade_payload(**payload_kwargs),
        ok=ok,
        status=status,
    )


def _allianz_config():
    return {
        "url": ALLIANZ_URL,
        "method": "playwright",
        "issuer": "Allianz",
        "internal_id": None,
        "official_logic": "shared_page=true",
    }


def test_parse_allianz_fund_options_matches_exact_code_with_extra_products():
    fund_no = official.parse_allianz_fund_options(OPTIONS_JSON, "00993A")

    assert fund_no == "E0002"


def test_parse_allianz_fund_options_fails_when_requested_code_is_missing():
    with pytest.raises(ValueError, match="00999A"):
        official.parse_allianz_fund_options(OPTIONS_JSON, "00999A")


def test_parse_allianz_fund_options_rejects_api_error_status():
    payload = json.loads(OPTIONS_JSON)
    payload["StatusCode"] = 500
    payload["Message"] = "service unavailable"

    with pytest.raises(ValueError, match="service unavailable"):
        official.parse_allianz_fund_options(json.dumps(payload), "00993A")


def test_parse_allianz_api_uses_five_column_headers_and_pcf_date():
    rows = official.parse_allianz_api(
        json.dumps(_trade_payload()),
        "00993A",
        ALLIANZ_URL,
        expected_fund_no="E0002",
    )

    assert len(rows) == 5
    assert rows[0]["etf_code"] == "00993A"
    assert rows[0]["stock_code"] == "2330"
    assert rows[0]["stock_name"] == "台積電"
    assert rows[0]["shares"] == 475000
    assert rows[0]["weight_pct"] == 11.51
    assert rows[0]["date"] == "2026/07/17"
    assert rows[0]["source_type"] == "official_fallback"
    assert rows[0]["extraction_method"] == "playwright_api_intercept"
    assert rows[4]["stock_name"] == "國巨*"


def test_parse_allianz_api_rejects_api_error_status():
    payload = _trade_payload()
    payload["StatusCode"] = 500
    payload["Message"] = "trade service unavailable"

    with pytest.raises(ValueError, match="trade service unavailable"):
        official.parse_allianz_api(
            json.dumps(payload),
            "00993A",
            ALLIANZ_URL,
            expected_fund_no="E0002",
        )


def test_parse_allianz_api_rejects_unknown_stock_schema():
    payload = _trade_payload()
    payload["Entries"]["DynamicTableData"][0]["Columns"] = [
        {"Name": "序號"},
        {"Name": "未知欄位"},
    ]

    with pytest.raises(ValueError, match="schema"):
        official.parse_allianz_api(
            json.dumps(payload),
            "00993A",
            ALLIANZ_URL,
            expected_fund_no="E0002",
        )


def test_parse_allianz_api_skips_futures_table():
    rows = official.parse_allianz_api(
        json.dumps(_trade_payload()),
        "00993A",
        ALLIANZ_URL,
        expected_fund_no="E0002",
    )

    assert {row["asset_type"] for row in rows} == {"stock"}
    assert all(row["stock_code"] != "TX" for row in rows)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (_trade_payload(etf_code="00984A"), "00984A"),
        (_trade_payload(fund_no="E0001"), "E0001"),
        (_trade_payload(pcf_date=None), "date"),
        (_trade_payload(include_stock_table=False), "stock"),
    ],
)
def test_parse_allianz_api_fails_closed_on_identity_or_shape_errors(payload, message):
    with pytest.raises(ValueError, match=message):
        official.parse_allianz_api(
            json.dumps(payload),
            "00993A",
            ALLIANZ_URL,
            expected_fund_no="E0002",
        )


def test_allianz_response_predicates_require_post_and_exact_endpoint():
    options = _options_response()
    trade = _trade_response()

    assert official._is_allianz_fund_options_response(options) is True
    assert official._is_allianz_trade_info_response(trade) is True
    assert official._is_allianz_trade_info_response(
        _FakeResponse(TRADE_URL, _trade_payload(), method="GET")
    ) is False
    assert official._is_allianz_trade_info_response(
        _FakeResponse(f"{TRADE_URL}/extra", _trade_payload())
    ) is False
    assert official._is_allianz_trade_info_response(
        _FakeResponse(
            "https://evil.example/webapi/api/Fund/GetFundTradeInfo",
            _trade_payload(),
        )
    ) is False


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config", return_value=_allianz_config())
async def test_scrape_allianz_uses_matching_initial_default_response(mock_config):
    page, combobox, option, queued = _mock_page(
        [
            _options_response(),
            _trade_response(etf_code="00984A", fund_no="E0001"),
        ],
        option_code="00984A",
    )

    result = await official.scrape_allianz_playwright("00984A", page)

    assert result["ok"] is True
    assert len(result["stock_rows"]) == 5
    assert {row["etf_code"] for row in result["stock_rows"]} == {"00984A"}
    assert queued == []
    page.goto.assert_awaited_once_with(
        ALLIANZ_URL,
        wait_until="domcontentloaded",
        timeout=60000,
    )
    page.locator.assert_not_called()
    combobox.click.assert_not_awaited()
    option.click.assert_not_awaited()
    assert not page.request.method_calls


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config", return_value=_allianz_config())
async def test_scrape_allianz_switches_by_exact_code_and_intercepts_response(mock_config):
    page, combobox, option, queued = _mock_page(
        [
            _options_response(),
            _trade_response(etf_code="00984A", fund_no="E0001"),
            _trade_response(etf_code="00993A", fund_no="E0002"),
        ]
    )

    result = await official.scrape_allianz_playwright("00993A", page)

    assert result["ok"] is True
    assert len(result["stock_rows"]) == 5
    assert {row["etf_code"] for row in result["stock_rows"]} == {"00993A"}
    assert queued == []
    assert page.expect_response.call_count == 3
    assert page.locator.call_args_list == [
        call(COMBOBOX_SELECTOR),
        call('[role="option"][aria-label^="00993A "]'),
    ]
    combobox.count.assert_awaited_once()
    combobox.click.assert_awaited_once()
    option.wait_for.assert_awaited_once_with(state="visible", timeout=10_000)
    option.count.assert_awaited_once()
    option.click.assert_awaited_once()
    assert not page.request.method_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("option_count", [0, 2])
@patch("scrapers.official.get_official_config", return_value=_allianz_config())
async def test_scrape_allianz_fails_when_exact_option_is_missing_or_ambiguous(
    mock_config,
    option_count,
):
    page, _, option, queued = _mock_page(
        [
            _options_response(),
            _trade_response(etf_code="00984A", fund_no="E0001"),
        ],
        option_count=option_count,
    )

    result = await official.scrape_allianz_playwright("00993A", page)

    assert result["ok"] is False
    assert result["all_rows"] == []
    assert "00993A" in result["reason"]
    assert queued == []
    option.click.assert_not_awaited()


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config", return_value=_allianz_config())
async def test_scrape_allianz_rejects_mismatched_switched_response(mock_config):
    page, _, _, queued = _mock_page(
        [
            _options_response(),
            _trade_response(etf_code="00984A", fund_no="E0001"),
            _trade_response(etf_code="00984A", fund_no="E0001"),
        ]
    )

    result = await official.scrape_allianz_playwright("00993A", page)

    assert result["ok"] is False
    assert result["all_rows"] == []
    assert "mismatch" in result["reason"].lower()
    assert queued == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_response", ["options", "initial_trade", "switched_trade"])
@patch("scrapers.official.get_official_config", return_value=_allianz_config())
async def test_scrape_allianz_fails_closed_on_intercepted_http_error(
    mock_config,
    failed_response,
):
    options = _options_response()
    initial = _trade_response(etf_code="00984A", fund_no="E0001")
    switched = _trade_response(etf_code="00993A", fund_no="E0002")
    if failed_response == "options":
        options = _options_response(ok=False, status=503)
    elif failed_response == "initial_trade":
        initial = _trade_response(ok=False, status=503)
    else:
        switched = _trade_response(ok=False, status=503)

    page, _, _, queued = _mock_page([options, initial, switched])

    result = await official.scrape_allianz_playwright("00993A", page)

    assert result["ok"] is False
    assert result["all_rows"] == []
    assert "HTTP 503" in result["reason"]
    if failed_response != "switched_trade":
        assert len(queued) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("etf_code", ["00984A", "00993A"])
@patch("scrapers.official.get_official_config", return_value=_allianz_config())
async def test_dispatcher_routes_allianz_codes_to_handler(mock_config, etf_code):
    sentinel = {"ok": True, "stock_rows": [{"etf_code": etf_code}]}
    page = Mock()

    with patch.object(
        official,
        "scrape_allianz_playwright",
        new=AsyncMock(return_value=sentinel),
    ) as handler:
        result = await official.scrape_official_with_browser(etf_code, page)

    assert result is sentinel
    handler.assert_awaited_once_with(etf_code, page)
