from pathlib import Path


path = Path("scripts/scrapers/official.py")
text = path.read_text(encoding="utf-8")
old = '''def _is_successful_get_response(response) -> bool:
    if getattr(response, "ok", True) is False:
        return False
    request = getattr(response, "request", None)
    method = getattr(request, "method", "GET")
    return not isinstance(method, str) or method.upper() == "GET"


def _matches_api_endpoint(response, domain: str, path: str) -> bool:
    parsed = urlparse(_response_url(response))
    hostname = (parsed.hostname or "").lower()
    response_path = parsed.path.rstrip("/").lower()
    expected_domain = domain.lower()
    host_matches = hostname == expected_domain or hostname.endswith(
        f".{expected_domain}"
    )
    return (
        host_matches
        and response_path == path.lower()
        and _is_successful_get_response(response)
    )


def _is_capital_buyback_response(response) -> bool:
    return _matches_api_endpoint(
        response,
        "capitalfund.com.tw",
        "/cfweb/api/etf/buyback",
    )


def _is_nomura_assets_response(response) -> bool:
    return _matches_api_endpoint(
        response,
        "nomurafunds.com.tw",
        "/api/etfapi/api/fund/getfundassets",
    )


def _is_ctbc_holdings_response(response) -> bool:
    return _matches_api_endpoint(
        response,
        "ctbcinvestments.com.tw",
        "/api/etf/etfholdingweight",
    )
'''
new = '''def _has_expected_response_method(response, expected_method: str) -> bool:
    if getattr(response, "ok", True) is False:
        return False
    request = getattr(response, "request", None)
    method = getattr(request, "method", expected_method)
    return (
        not isinstance(method, str)
        or method.upper() == expected_method.upper()
    )


def _matches_api_endpoint(
    response,
    domain: str,
    path: str,
    expected_method: str,
) -> bool:
    parsed = urlparse(_response_url(response))
    hostname = (parsed.hostname or "").lower()
    response_path = parsed.path.rstrip("/").lower()
    expected_domain = domain.lower()
    host_matches = hostname == expected_domain or hostname.endswith(
        f".{expected_domain}"
    )
    return (
        host_matches
        and response_path == path.lower()
        and _has_expected_response_method(response, expected_method)
    )


def _is_capital_buyback_response(response) -> bool:
    return _matches_api_endpoint(
        response,
        "capitalfund.com.tw",
        "/cfweb/api/etf/buyback",
        "POST",
    )


def _is_nomura_assets_response(response) -> bool:
    return _matches_api_endpoint(
        response,
        "nomurafunds.com.tw",
        "/api/etfapi/api/fund/getfundassets",
        "POST",
    )


def _is_ctbc_holdings_response(response) -> bool:
    return _matches_api_endpoint(
        response,
        "ctbcinvestments.com.tw",
        "/api/etf/etfholdingweight",
        "GET",
    )
'''

if old not in text:
    raise SystemExit("expected official response matcher block not found")

path.write_text(text.replace(old, new, 1), encoding="utf-8")
