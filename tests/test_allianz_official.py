import json
from unittest.mock import AsyncMock, Mock, call, patch

import pytest

import scrapers.official as official


ALLIANZ_URL = "https://etf.allianzgi.com.tw/list-trade"
OPTIONS_URL = (
    "https://etf.allianzgi.com.tw/webapi/api/Category/GetFundDropdownOptions"
)
TRADE_URL = "https://etf.allianzgi.com.tw/webapi/api/Fund/GetFundTradeInfo"


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


class _FakeApiResponse:
    def __init__(self, payload, *, ok=True, status=200):
        self._body = json.dumps(payload)
        self.ok = ok
        self.status = status

    async def text(self):
        return self._body


def _mock_page(*responses):
    request_context = Mock()
    request_context.post = AsyncMock(side_effect=responses)
    page = Mock()
    page.goto = AsyncMock()
    page.request = request_context
    return page


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


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config", return_value=_allianz_config())
async def test_scrape_allianz_posts_exact_fund_mapping_and_trade_request(mock_config):
    page = _mock_page(
        _FakeApiResponse(json.loads(OPTIONS_JSON)),
        _FakeApiResponse(_trade_payload()),
    )

    result = await official.scrape_allianz_playwright("00993A", page)

    assert result["ok"] is True
    assert len(result["stock_rows"]) == 5
    assert result["stock_rows"][0]["etf_code"] == "00993A"
    page.goto.assert_awaited_once_with(
        ALLIANZ_URL,
        wait_until="domcontentloaded",
        timeout=60000,
    )
    assert page.request.post.await_args_list == [
        call(
            OPTIONS_URL,
            data={"TypeID": -1, "IsAddAllOption": False},
        ),
        call(
            TRADE_URL,
            data={"Date": None, "FundNo": "E0002"},
        ),
    ]


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config", return_value=_allianz_config())
async def test_scrape_allianz_does_not_accept_default_fund_response(mock_config):
    page = _mock_page(
        _FakeApiResponse(json.loads(OPTIONS_JSON)),
        _FakeApiResponse(_trade_payload(etf_code="00984A", fund_no="E0001")),
    )

    result = await official.scrape_allianz_playwright("00993A", page)

    assert result["ok"] is False
    assert result["all_rows"] == []
    assert "mismatch" in result["reason"].lower()


@pytest.mark.asyncio
@patch("scrapers.official.get_official_config", return_value=_allianz_config())
async def test_scrape_allianz_fails_when_requested_option_is_missing(mock_config):
    page = _mock_page(_FakeApiResponse(json.loads(OPTIONS_JSON)))

    result = await official.scrape_allianz_playwright("00999A", page)

    assert result["ok"] is False
    assert result["all_rows"] == []
    assert "00999A" in result["reason"]
    page.request.post.assert_awaited_once()


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
        create=True,
    ) as handler:
        result = await official.scrape_official_with_browser(etf_code, page)

    assert result is sentinel
    handler.assert_awaited_once_with(etf_code, page)
