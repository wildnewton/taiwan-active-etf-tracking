from pathlib import Path


path = Path("scripts/scrapers/official.py")
text = path.read_text(encoding="utf-8")

text = text.replace(
    'EXTRACTION_METHOD_API_REQUEST = "playwright_api_request"\n',
    "",
    1,
)
text = text.replace(
    '_ALLIANZ_TRADE_INFO_PATH = "/webapi/api/Fund/GetFundTradeInfo"\n',
    '_ALLIANZ_TRADE_INFO_PATH = "/webapi/api/Fund/GetFundTradeInfo"\n'
    '_ALLIANZ_COMBOBOX_SELECTOR = \'[role="combobox"][aria-label*="主動安聯"]\'\n',
    1,
)

old_parse_start = '''def parse_allianz_api(
    trade_json: str,
    etf_code: str,
    source_url: str,
    *,
    expected_fund_no: str,
) -> list[dict]:
    """Parse one exact Allianz fund trade-info response."""
    data = json.loads(trade_json)
    if not isinstance(data, dict) or data.get("StatusCode") != 0:
        message = data.get("Message") if isinstance(data, dict) else "invalid payload"
        raise ValueError(f"Allianz trade info API failed: {message}")
    entries = data.get("Entries", {})
    if not isinstance(entries, dict):
        raise ValueError("Allianz trade response entries missing")

'''
new_parse_start = '''def _parse_allianz_trade_entries(trade_json: str) -> dict:
    data = json.loads(trade_json)
    if not isinstance(data, dict) or data.get("StatusCode") != 0:
        message = data.get("Message") if isinstance(data, dict) else "invalid payload"
        raise ValueError(f"Allianz trade info API failed: {message}")
    entries = data.get("Entries", {})
    if not isinstance(entries, dict):
        raise ValueError("Allianz trade response entries missing")
    return entries


def _parse_allianz_trade_identity(trade_json: str) -> tuple[str, str]:
    entries = _parse_allianz_trade_entries(trade_json)
    etf_code = str(entries.get("CSecuritiesCode") or "").strip().upper()
    fund_no = str(entries.get("CFundId") or "").strip()
    if not etf_code or not fund_no:
        raise ValueError("Allianz trade response identity missing")
    return etf_code, fund_no


def parse_allianz_api(
    trade_json: str,
    etf_code: str,
    source_url: str,
    *,
    expected_fund_no: str,
) -> list[dict]:
    """Parse one exact Allianz fund trade-info response."""
    entries = _parse_allianz_trade_entries(trade_json)

'''
if old_parse_start not in text:
    raise SystemExit("Allianz parser marker not found")
text = text.replace(old_parse_start, new_parse_start, 1)
text = text.replace(
    "                EXTRACTION_METHOD_API_REQUEST,\n",
    "                EXTRACTION_METHOD_API,\n",
    1,
)

handler_start = text.index("def _allianz_api_url(")
handler_end = text.index("async def scrape_mega_playwright(", handler_start)
new_handler = '''async def _allianz_response_text(response, label: str) -> str:
    if getattr(response, "ok", True) is False:
        raise ValueError(
            f"Allianz {label} API HTTP {getattr(response, 'status', 'error')}"
        )
    return await response.text()


async def _switch_allianz_fund(etf_code: str, page) -> str:
    combobox = page.locator(_ALLIANZ_COMBOBOX_SELECTOR)
    if await combobox.count() != 1:
        raise ValueError(f"Allianz fund selector not found for {etf_code}")
    await combobox.click()

    option_selector = f'[role="option"][aria-label^="{etf_code} "]'
    option = page.locator(option_selector)
    try:
        await option.wait_for(state="visible", timeout=_API_RESPONSE_TIMEOUT_MS)
    except (PlaywrightTimeoutError, PlaywrightError) as exc:
        raise ValueError(f"Allianz fund option not found for {etf_code}") from exc
    if await option.count() != 1:
        raise ValueError(
            f"Allianz fund option not found or ambiguous for {etf_code}"
        )

    async with page.expect_response(
        _is_allianz_trade_info_response,
        timeout=_API_RESPONSE_TIMEOUT_MS,
    ) as response_info:
        await option.click()
    response = await response_info.value
    return await _allianz_response_text(response, "trade info")


async def scrape_allianz_playwright(etf_code: str, page) -> dict:
    etf_code = etf_code.upper()
    config = get_official_config(etf_code)
    source_url = config["url"]
    navigation_completed = False

    try:
        async with page.expect_response(
            _is_allianz_fund_options_response,
            timeout=_API_RESPONSE_TIMEOUT_MS,
        ) as options_info:
            async with page.expect_response(
                _is_allianz_trade_info_response,
                timeout=_API_RESPONSE_TIMEOUT_MS,
            ) as trade_info:
                await page.goto(
                    source_url,
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                navigation_completed = True
        options_response = await options_info.value
        initial_trade_response = await trade_info.value
    except (PlaywrightTimeoutError, PlaywrightError):
        if not navigation_completed:
            raise
        return _failed_result(
            source_url,
            "Allianz initial APIs not intercepted",
        )

    try:
        options_body = await _allianz_response_text(
            options_response,
            "fund options",
        )
        expected_fund_no = parse_allianz_fund_options(options_body, etf_code)

        initial_trade_body = await _allianz_response_text(
            initial_trade_response,
            "trade info",
        )
        initial_code, initial_fund_no = _parse_allianz_trade_identity(
            initial_trade_body
        )
        if (initial_code, initial_fund_no) == (etf_code, expected_fund_no):
            trade_body = initial_trade_body
        else:
            trade_body = await _switch_allianz_fund(etf_code, page)

        all_rows = parse_allianz_api(
            trade_body,
            etf_code,
            source_url,
            expected_fund_no=expected_fund_no,
        )
    except Exception as exc:
        return _failed_result(source_url, f"Allianz API error: {exc}")

    return _build_result(all_rows, source_url, EXTRACTION_METHOD_API)


'''
text = text[:handler_start] + new_handler + text[handler_end:]

predicate_marker = '''def _is_ctbc_holdings_response(response) -> bool:
    return _matches_api_endpoint(
        response,
        "ctbcinvestments.com.tw",
        "/api/etf/etfholdingweight",
    )


'''
predicate_code = '''def _matches_post_api_endpoint(response, domain: str, path: str) -> bool:
    parsed = urlparse(_response_url(response))
    hostname = (parsed.hostname or "").lower()
    response_path = parsed.path.rstrip("/").lower()
    expected_domain = domain.lower()
    host_matches = hostname == expected_domain or hostname.endswith(
        f".{expected_domain}"
    )
    request = getattr(response, "request", None)
    method = getattr(request, "method", "")
    return (
        host_matches
        and response_path == path.lower()
        and isinstance(method, str)
        and method.upper() == "POST"
    )


def _is_allianz_fund_options_response(response) -> bool:
    return _matches_post_api_endpoint(
        response,
        "etf.allianzgi.com.tw",
        _ALLIANZ_FUND_OPTIONS_PATH,
    )


def _is_allianz_trade_info_response(response) -> bool:
    return _matches_post_api_endpoint(
        response,
        "etf.allianzgi.com.tw",
        _ALLIANZ_TRADE_INFO_PATH,
    )


'''
if predicate_marker not in text:
    raise SystemExit("CTBC predicate marker not found")
text = text.replace(predicate_marker, predicate_marker + predicate_code, 1)

path.write_text(text, encoding="utf-8")
