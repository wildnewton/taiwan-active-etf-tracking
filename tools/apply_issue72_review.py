from pathlib import Path


official_path = Path("scripts/scrapers/official.py")
official = official_path.read_text(encoding="utf-8")

official = official.replace(
    'EXTRACTION_METHOD_API = "playwright_api_intercept"\n',
    'EXTRACTION_METHOD_API = "playwright_api_intercept"\n'
    'EXTRACTION_METHOD_API_REQUEST = "playwright_api_request"\n',
    1,
)

official = official.replace(
    '''def parse_allianz_fund_options(options_json: str, etf_code: str) -> str:
    """Return Allianz's internal FundNo for one exact exchange ETF code."""
    data = json.loads(options_json)
    entries = data.get("Entries", []) if isinstance(data, dict) else []
''',
    '''def parse_allianz_fund_options(options_json: str, etf_code: str) -> str:
    """Return Allianz's internal FundNo for one exact exchange ETF code."""
    data = json.loads(options_json)
    if not isinstance(data, dict) or data.get("StatusCode") != 0:
        message = data.get("Message") if isinstance(data, dict) else "invalid payload"
        raise ValueError(f"Allianz fund options API failed: {message}")
    entries = data.get("Entries", [])
''',
    1,
)

official = official.replace(
    '''    data = json.loads(trade_json)
    entries = data.get("Entries", {}) if isinstance(data, dict) else {}
    if not isinstance(entries, dict):
''',
    '''    data = json.loads(trade_json)
    if not isinstance(data, dict) or data.get("StatusCode") != 0:
        message = data.get("Message") if isinstance(data, dict) else "invalid payload"
        raise ValueError(f"Allianz trade info API failed: {message}")
    entries = data.get("Entries", {})
    if not isinstance(entries, dict):
''',
    1,
)

official = official.replace(
    '''    stock_table = None
    for table in entries.get("DynamicTableData", []):
''',
    '''    tables = entries.get("DynamicTableData")
    if not isinstance(tables, list):
        raise ValueError("Allianz holdings tables missing")

    stock_table = None
    for table in tables:
''',
    1,
)

official = official.replace(
    '''    columns = stock_table.get("Columns", [])
    headers = [
''',
    '''    columns = stock_table.get("Columns")
    if not isinstance(columns, list):
        raise ValueError("Allianz stock table schema invalid")
    headers = [
''',
    1,
)

official = official.replace(
    '''    rows = []
    for raw in stock_table.get("Rows", []):
''',
    '''    raw_rows = stock_table.get("Rows")
    if not isinstance(raw_rows, list):
        raise ValueError("Allianz stock table rows invalid")

    rows = []
    for raw in raw_rows:
''',
    1,
)

parser_start = official.index("def parse_allianz_api(")
parser_end = official.index("def parse_mega_text(", parser_start)
parser = official[parser_start:parser_end].replace(
    "EXTRACTION_METHOD_API,",
    "EXTRACTION_METHOD_API_REQUEST,",
)
official = official[:parser_start] + parser + official[parser_end:]

handler_start = official.index("async def scrape_allianz_playwright(")
handler_end = official.index("async def scrape_mega_playwright(", handler_start)
handler = official[handler_start:handler_end].replace(
    "return _build_result(all_rows, source_url, EXTRACTION_METHOD_API)",
    "return _build_result(all_rows, source_url, EXTRACTION_METHOD_API_REQUEST)",
)
official = official[:handler_start] + handler + official[handler_end:]

official = official.replace(
    "return dedupe_rows(rows)\n\n\n\ndef parse_allianz_fund_options",
    "return dedupe_rows(rows)\n\n\ndef parse_allianz_fund_options",
    1,
)
official = official.replace(
    "return _build_result(all_rows, source_url, EXTRACTION_METHOD_API)\n\n\n\ndef _allianz_api_url",
    "return _build_result(all_rows, source_url, EXTRACTION_METHOD_API)\n\n\ndef _allianz_api_url",
    1,
)

official_path.write_text(official, encoding="utf-8")


test_path = Path("tests/test_allianz_official.py")
test = test_path.read_text(encoding="utf-8")
test = test.replace(
    'assert rows[0]["extraction_method"] == "playwright_api_intercept"',
    'assert rows[0]["extraction_method"] == "playwright_api_request"',
    1,
)

test = test.replace(
    '''def test_parse_allianz_fund_options_fails_when_requested_code_is_missing():
    with pytest.raises(ValueError, match="00999A"):
        official.parse_allianz_fund_options(OPTIONS_JSON, "00999A")


''',
    '''def test_parse_allianz_fund_options_fails_when_requested_code_is_missing():
    with pytest.raises(ValueError, match="00999A"):
        official.parse_allianz_fund_options(OPTIONS_JSON, "00999A")


def test_parse_allianz_fund_options_rejects_api_error_status():
    payload = json.loads(OPTIONS_JSON)
    payload["StatusCode"] = 500
    payload["Message"] = "service unavailable"

    with pytest.raises(ValueError, match="service unavailable"):
        official.parse_allianz_fund_options(json.dumps(payload), "00993A")


''',
    1,
)

test = test.replace(
    '''def test_parse_allianz_api_skips_futures_table():
''',
    '''def test_parse_allianz_api_rejects_api_error_status():
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
''',
    1,
)

test = test.replace(
    '''@pytest.mark.asyncio
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
''',
    '''@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("etf_code", "fund_no"),
    [("00984A", "E0001"), ("00993A", "E0002")],
)
@patch("scrapers.official.get_official_config", return_value=_allianz_config())
async def test_scrape_allianz_posts_exact_fund_mapping_and_trade_request(
    mock_config,
    etf_code,
    fund_no,
):
    page = _mock_page(
        _FakeApiResponse(json.loads(OPTIONS_JSON)),
        _FakeApiResponse(_trade_payload(etf_code=etf_code, fund_no=fund_no)),
    )

    result = await official.scrape_allianz_playwright(etf_code, page)

    assert result["ok"] is True
    assert len(result["stock_rows"]) == 5
    assert result["stock_rows"][0]["etf_code"] == etf_code
''',
    1,
)

test = test.replace(
    '''            data={"Date": None, "FundNo": "E0002"},
''',
    '''            data={"Date": None, "FundNo": fund_no},
''',
    1,
)

insert_marker = '''@pytest.mark.asyncio
@patch("scrapers.official.get_official_config", return_value=_allianz_config())
async def test_scrape_allianz_does_not_accept_default_fund_response(mock_config):
'''
http_tests = '''@pytest.mark.asyncio
@pytest.mark.parametrize("failed_request", ["options", "trade"])
@patch("scrapers.official.get_official_config", return_value=_allianz_config())
async def test_scrape_allianz_fails_closed_on_http_error(mock_config, failed_request):
    failed = _FakeApiResponse({}, ok=False, status=503)
    responses = [failed]
    if failed_request == "trade":
        responses = [_FakeApiResponse(json.loads(OPTIONS_JSON)), failed]
    page = _mock_page(*responses)

    result = await official.scrape_allianz_playwright("00993A", page)

    assert result["ok"] is False
    assert result["all_rows"] == []
    assert "HTTP 503" in result["reason"]


'''
if http_tests not in test:
    test = test.replace(insert_marker, http_tests + insert_marker, 1)

test_path.write_text(test, encoding="utf-8")
