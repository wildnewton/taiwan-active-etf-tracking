import inspect
import json
from datetime import date
from unittest.mock import AsyncMock, Mock, patch

import pytest
import requests

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


class _DirectResponse:
    def __init__(self, url, payload, *, status_code=200):
        self.url = url
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.text = payload if isinstance(payload, str) else json.dumps(payload)
        self.content = self.text.encode()

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


def _direct_post_side_effect(trade_payload, *, trade_status=200):
    def post(url, **kwargs):
        if url == OPTIONS_URL:
            return _DirectResponse(url, json.loads(OPTIONS_JSON))
        if url == TRADE_URL:
            return _DirectResponse(url, trade_payload, status_code=trade_status)
        raise AssertionError(f"unexpected POST URL: {url}")

    return post


async def _call_direct_handler(
    etf_code,
    target_date,
    trade_payload,
    *,
    trade_status=200,
):
    with (
        patch("scrapers.official.get_official_config", return_value=_allianz_config()),
        patch(
            "scrapers.official.requests.post",
            side_effect=_direct_post_side_effect(
                trade_payload,
                trade_status=trade_status,
            ),
        ) as post,
    ):
        result = official.scrape_allianz_api(etf_code, target_date)
        if inspect.isawaitable(result):
            result = await result
    return result, post


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


@pytest.mark.asyncio
async def test_scrape_allianz_posts_exact_fund_and_taipei_midnight_payload():
    result, post = await _call_direct_handler(
        "00984A",
        date(2026, 7, 17),
        _trade_payload(
            etf_code="00984A",
            fund_no="E0001",
            pcf_date="2026-07-17T00:00:00",
        ),
    )

    trade_calls = [call for call in post.call_args_list if call.args == (TRADE_URL,)]
    assert len(trade_calls) == 1
    assert trade_calls[0].kwargs["json"] == {
        "Date": "2026-07-16T16:00:00.000Z",
        "FundNo": "E0001",
    }
    assert result["ok"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("etf_code", ["00984A", "00993A"])
async def test_dispatcher_routes_every_configured_allianz_etf_to_direct_handler(
    etf_code,
):
    target_date = date(2026, 7, 17)
    sentinel = {"ok": True, "stock_rows": [{"etf_code": etf_code}]}

    class AwaitableResult(dict):
        def __await__(self):
            async def resolve():
                return self

            return resolve().__await__()

    handler_result = AwaitableResult(sentinel)
    direct_handler = Mock(return_value=handler_result)
    legacy_handler = AsyncMock(return_value={"ok": False, "reason": "legacy path"})
    page = Mock()
    page.goto = AsyncMock()
    page.locator = Mock()
    page.expect_response = Mock()
    option = Mock()
    option.click = AsyncMock()
    page.locator.return_value = option

    with (
        patch("scrapers.official.get_official_config", return_value=_allianz_config()),
        patch.object(
            official,
            "scrape_allianz_api",
            new=direct_handler,
            create=True,
        ),
        patch.object(
            official,
            "scrape_allianz_playwright",
            new=legacy_handler,
            create=True,
        ),
    ):
        result = await official.scrape_official_with_browser(
            etf_code,
            page,
            target_date=target_date,
        )

    assert result is handler_result
    direct_handler.assert_called_once_with(etf_code, target_date)
    legacy_handler.assert_not_awaited()
    page.goto.assert_not_awaited()
    page.locator.assert_not_called()
    page.expect_response.assert_not_called()
    option.click.assert_not_awaited()


def _invalid_trade_case(case):
    if case == "http_error":
        return _trade_payload(), 503
    if case == "api_error":
        payload = _trade_payload()
        payload["StatusCode"] = 500
        return payload, 200
    if case == "empty_entries":
        return {"StatusCode": 0, "Entries": {}}, 200
    if case == "empty_stock_rows":
        payload = _trade_payload()
        payload["Entries"]["DynamicTableData"][0]["Rows"] = []
        return payload, 200
    if case == "invalid_json":
        return "{not-json", 200
    if case == "schema_change":
        payload = _trade_payload()
        payload["Entries"]["DynamicTableData"][0]["Columns"] = [
            {"Name": "未知欄位"}
        ]
        return payload, 200
    if case == "fund_mismatch":
        return _trade_payload(fund_no="E0001"), 200
    if case == "etf_mismatch":
        return _trade_payload(etf_code="00984A"), 200
    if case == "date_mismatch":
        return _trade_payload(pcf_date="2026-07-18T00:00:00"), 200
    raise AssertionError(f"unknown case: {case}")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "http_error",
        "api_error",
        "empty_entries",
        "empty_stock_rows",
        "invalid_json",
        "schema_change",
        "fund_mismatch",
        "etf_mismatch",
        "date_mismatch",
    ],
)
async def test_scrape_allianz_fails_closed_for_invalid_trade_response(case):
    trade_payload, trade_status = _invalid_trade_case(case)

    result, _ = await _call_direct_handler(
        "00993A",
        date(2026, 7, 17),
        trade_payload,
        trade_status=trade_status,
    )

    assert result["ok"] is False
    assert result["all_rows"] == []
    assert result["stock_rows"] == []


@pytest.mark.asyncio
async def test_scrape_allianz_success_preserves_downstream_shape_and_metadata():
    result, _ = await _call_direct_handler(
        "00993A",
        date(2026, 7, 17),
        _trade_payload(),
    )

    assert set(result) == {
        "ok",
        "reason",
        "all_rows",
        "stock_rows",
        "non_stock_rows",
        "source_url",
        "source_type",
        "total_weight_all_rows",
        "total_weight_stock_rows",
    }
    assert result["ok"] is True
    assert result["reason"] == "ok"
    assert result["source_type"] == "official_fallback"
    assert result["source_url"] == ALLIANZ_URL
    assert result["non_stock_rows"] == []
    assert result["all_rows"] == result["stock_rows"]
    assert len(result["stock_rows"]) == 5
    assert {
        row["extraction_method"] for row in result["stock_rows"]
    } == {"playwright_api_intercept"}
